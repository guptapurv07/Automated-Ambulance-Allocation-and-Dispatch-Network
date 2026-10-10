"""Emergency call intake.

Intake is the *producer* side of the system. It validates the call, passes it
through the deduplication gate, places a new incident on the dispatch queue
and returns immediately with 202 Accepted. It does not allocate a vehicle.

A call that repeats an emergency already on record is not queued again. It is
counted against the existing incident, so several bystanders reporting one
accident produce one incident and one ambulance.

Allocation happens on a worker thread (app/core/dispatcher.py). Keeping the
HTTP thread free is what allows many simultaneous callers to be accepted
without one waiting behind another's database work -- and it is what creates
the contention that the row locking exists to resolve.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.dedup import register_report
from app.core.queue import DispatchRequest
from app.core.runtime import dispatch_queue
from app.db import get_session
from app.models import Incident
from app.schemas import (
    AllocationResult,
    AmbulanceOut,
    DispatchOut,
    HospitalOut,
    IncidentAccepted,
    IncidentCreate,
    IncidentOut,
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
    # The gate commits before returning: the emergency is on record even if
    # allocation later fails, and the worker needs a row it can load by id.
    incident, mav_duplicate = register_report(
        session,
        lat=payload.lat,
        lon=payload.lon,
        severity=payload.severity,
        incident_type=payload.incident_type,
        caller_phone=payload.caller_phone,
        description=payload.description,
        dedup_enabled=settings.dedup_enabled,
    )

    if mav_duplicate:
        logger.info(
            "incident=%s DUPLICATE report merged  type=%s reports=%d",
            incident.id,
            incident.incident_type.value,
            incident.report_count,
        )
        return IncidentAccepted(
            incident_id=incident.id,
            status=incident.status,
            duplicate=True,
            report_count=incident.report_count,
            queue_depth=dispatch_queue.depth,
            message=(
                f"Already reported: added to incident {incident.id} "
                f"({incident.report_count} reports so far)"
            ),
            poll=f"/incidents/{incident.id}",
        )

    dispatch_queue.put(
        DispatchRequest(
            incident_id=incident.id, severity=incident.severity.value
        )
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

    return IncidentAccepted(
        incident_id=incident.id,
        status=incident.status,
        queue_depth=dispatch_queue.depth,
        message="Emergency accepted and queued for dispatch",
        poll=f"/incidents/{incident.id}",
    )


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

    dispatch = incident.dispatch
    return AllocationResult(
        incident=IncidentOut.model_validate(incident),
        dispatch=DispatchOut.model_validate(dispatch) if dispatch else None,
        ambulance=(
            AmbulanceOut.model_validate(dispatch.ambulance) if dispatch else None
        ),
        hospital=(
            HospitalOut.model_validate(dispatch.hospital)
            if dispatch and dispatch.hospital
            else None
        ),
        message=(
            f"Ambulance {dispatch.ambulance.call_sign} dispatched"
            if dispatch
            else f"Awaiting allocation (incident is {incident.status.value})"
        ),
    )
