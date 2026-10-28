"""Emergency call intake.

Intake is the *producer* side of the system. It validates the call, passes it
through the deduplication gate, places a new incident on the dispatch queue
and returns immediately with 202 Accepted. It does not allocate a vehicle.

A call that repeats an emergency already on record is not queued again. It is
counted against the existing incident, so several bystanders reporting one
accident produce one incident and one ambulance.

A call can also say how many people are injured. One ambulance is requested
for each patient, however many callers report the accident.

Allocation happens on a worker thread (app/core/dispatcher.py). Keeping the
HTTP thread free is what allows many simultaneous callers to be accepted
without one waiting behind another's database work -- and it is what creates
the contention that the row locking exists to resolve.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.core.dedup import register_report
from app.core.queue import QueueClosed
from app.core.runtime import dispatch_queue
from app.core.scheduling import fleet_positions, make_request
from app.db import get_session
from app.models import Dispatch, Incident, IncidentStatus, Severity
from app.schemas import (
    AllocationResult,
    AmbulanceOut,
    DispatchOut,
    HospitalOut,
    IncidentAccepted,
    IncidentCreate,
    IncidentOut,
    UnitOut,
)

logger = logging.getLogger("dispatch.intake")

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post(
    "",
    response_model=IncidentAccepted,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def report_incident(
    payload: IncidentCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> IncidentAccepted:
    """Accept an emergency call and queue it for allocation."""
    if not settings.in_service_area(payload.lat, payload.lon):
        logger.warning(
            "call refused: (%.4f, %.4f) is outside the service area",
            payload.lat,
            payload.lon,
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "This location is outside the service area (Dehradun), so no "
                "ambulance can be sent from here. Check the coordinates."
            ),
        )

    # The gate commits before returning: the emergency is on record even if
    # allocation later fails, and the worker needs a row it can load by id.
    incident, mav_duplicate, mav_new_rows = register_report(
        session,
        lat=payload.lat,
        lon=payload.lon,
        severity=payload.severity,
        incident_type=payload.incident_type,
        caller_phone=payload.caller_phone,
        description=payload.description,
        dedup_enabled=settings.dedup_enabled,
        patients=payload.patients,
    )

    # One queue entry for each row this call created, which is one for each
    # ambulance it asked for. The entry carries the deadline and expected
    # travel time, which is what the scheduler orders by. A plain repeat
    # report created nothing, so it queues nothing.
    mav_requests = []
    if mav_new_rows:
        mav_fleet = fleet_positions(session)
        mav_requests = [
            make_request(mav_row, mav_fleet, settings) for mav_row in mav_new_rows
        ]
    mav_new_ids = [mav_row.id for mav_row in mav_new_rows]

    # All database work for this call is done, so the connection goes back to
    # the pool now. Waiting for the framework to clean the request up is not
    # safe under load: that clean-up runs on the same threads that serve
    # requests, so in a burst it queues behind the waiting calls, and the
    # connections held until then are what run the pool dry.
    session.close()

    try:
        for mav_request in mav_requests:
            dispatch_queue.put(mav_request)
    except QueueClosed:
        # The server is shutting down. The call is already stored, and it is
        # put back on the queue when the server next starts.
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"The service is shutting down. Call {incident.id} is recorded "
                "and will be dispatched when the service is back."
            ),
        ) from None

    if mav_duplicate:
        logger.info(
            "incident=%s DUPLICATE report merged  type=%s reports=%d",
            incident.id,
            incident.incident_type.value,
            incident.report_count,
        )
        mav_message = (
            f"Already reported: added to incident {incident.id} "
            f"({incident.report_count} reports so far)"
        )
        if mav_new_ids:
            logger.info(
                "incident=%s QUEUED %d more ambulance request(s) for patients "
                "reported by a repeat caller: %s",
                incident.id,
                len(mav_new_ids),
                mav_new_ids,
            )
            mav_more = (
                "1 more ambulance was requested"
                if len(mav_new_ids) == 1
                else f"{len(mav_new_ids)} more ambulances were requested"
            )
            mav_message += (
                f". This caller reported more patients, so {mav_more}, "
                f"{incident.patients} in total"
            )
        return IncidentAccepted(
            incident_id=incident.id,
            status=incident.status,
            duplicate=True,
            report_count=incident.report_count,
            patients=incident.patients,
            incident_ids=mav_new_ids,
            queue_depth=dispatch_queue.depth,
            message=mav_message,
            poll=f"/incidents/{incident.id}",
        )

    logger.info(
        "incident=%s QUEUED type=%s severity=%s at (%.5f, %.5f)  queue_depth=%d",
        incident.id,
        incident.incident_type.value,
        incident.severity.value,
        incident.lat,
        incident.lon,
        dispatch_queue.depth,
    )
    if incident.patients > 1:
        logger.info(
            "incident=%s has %d patients: one ambulance requested for each, "
            "as incidents %s",
            incident.id,
            incident.patients,
            mav_new_ids,
        )

    return IncidentAccepted(
        incident_id=incident.id,
        status=incident.status,
        patients=incident.patients,
        incident_ids=mav_new_ids,
        queue_depth=dispatch_queue.depth,
        message=(
            "Emergency accepted and queued for dispatch"
            if incident.patients == 1
            else f"Emergency accepted: {incident.patients} ambulances requested"
        ),
        poll=f"/incidents/{incident.id}",
    )


@router.get("", response_model=list[IncidentOut])
def list_incidents(
    status: IncidentStatus | None = Query(
        default=None, description="Filter by incident status"
    ),
    severity: Severity | None = Query(default=None, description="Filter by severity"),
    top_level: bool = Query(
        default=False,
        description="Only the first row of each incident: one row per accident, "
        "without the rows of its further patients",
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Rows per page"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
    session: Session = Depends(get_session),
) -> list[Incident]:
    """Incidents, newest first, one page at a time.

    An accident with several patients appears as one row per patient: the
    further patients' rows carry `parent_id`, the id of the accident's first
    row, which holds the number of patients and of callers. With
    `top_level=true` only those first rows are listed; GET /incidents/{id}
    then shows every patient of one of them.
    """
    # Newest first by primary key, as for dispatches: ids follow the order
    # of creation, and the primary key needs no extra index to sort by.
    mav_stmt = select(Incident).order_by(Incident.id.desc())
    if status is not None:
        mav_stmt = mav_stmt.where(Incident.status == status)
    if severity is not None:
        mav_stmt = mav_stmt.where(Incident.severity == severity)
    if top_level:
        mav_stmt = mav_stmt.where(Incident.parent_id.is_(None))
    mav_stmt = mav_stmt.limit(limit).offset(offset)
    return list(session.execute(mav_stmt).scalars().all())


@router.get("/{incident_id}", response_model=AllocationResult)
def get_incident(
    incident_id: int, session: Session = Depends(get_session)
) -> AllocationResult:
    """Current state of an incident, including its dispatch once allocated."""
    incident = session.execute(
        select(Incident).where(Incident.id == incident_id)
    ).scalar_one_or_none()

    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Several patients: every patient's row, this incident first.
    mav_rows = [incident]
    if incident.patients > 1:
        mav_rows += session.execute(
            select(Incident)
            .where(Incident.parent_id == incident.id)
            .order_by(Incident.id)
        ).scalars().all()

    # Each dispatch with its ambulance and hospital, all in one statement. Read
    # one after another, the three can disagree: the reply could show an
    # ambulance already on its way to hospital with no hospital chosen yet.
    mav_dispatches = {
        mav_dispatch.incident_id: mav_dispatch
        for mav_dispatch in session.execute(
            select(Dispatch)
            .where(Dispatch.incident_id.in_([mav_row.id for mav_row in mav_rows]))
            .options(joinedload(Dispatch.ambulance), joinedload(Dispatch.hospital))
        ).scalars()
    }
    mav_units = [_unit(mav_row, mav_dispatches.get(mav_row.id)) for mav_row in mav_rows]
    mav_own = mav_units[0]

    if incident.patients > 1:
        mav_sent = sum(1 for mav_unit in mav_units if mav_unit.dispatch)
        mav_message = f"{mav_sent} of {incident.patients} ambulances dispatched"
    elif mav_own.ambulance:
        mav_message = f"Ambulance {mav_own.ambulance.call_sign} dispatched"
    else:
        mav_message = f"Awaiting allocation (incident is {incident.status.value})"

    return AllocationResult(
        incident=IncidentOut.model_validate(incident),
        dispatch=mav_own.dispatch,
        ambulance=mav_own.ambulance,
        hospital=mav_own.hospital,
        units=mav_units if incident.patients > 1 else [],
        message=mav_message,
    )


def _unit(incident: Incident, dispatch: Dispatch | None) -> UnitOut:
    """One patient's row with the ambulance and hospital it has so far."""
    return UnitOut(
        incident_id=incident.id,
        status=incident.status,
        dispatch=DispatchOut.model_validate(dispatch) if dispatch else None,
        ambulance=AmbulanceOut.model_validate(dispatch.ambulance) if dispatch else None,
        hospital=(
            HospitalOut.model_validate(dispatch.hospital)
            if dispatch and dispatch.hospital
            else None
        ),
    )
