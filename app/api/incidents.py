"""Emergency call intake.

Today the intake handler allocates inline, so the whole pipeline is visible in
one place. The queue and worker thread pool will sit between the two steps
below: intake will persist the incident, hand it to the queue, and return
202 Accepted while a worker performs the allocation.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.allocation import NoAmbulanceAvailable, allocate_ambulance
from app.db import get_session
from app.models import Incident
from app.schemas import (
    AllocationResult,
    AmbulanceOut,
    DispatchOut,
    IncidentCreate,
    IncidentOut,
)

logger = logging.getLogger("dispatch.intake")

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", response_model=AllocationResult, status_code=http_status.HTTP_201_CREATED)
def report_incident(
    payload: IncidentCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AllocationResult:
    """Report an emergency and allocate the nearest available ambulance."""
    incident = Incident(
        lat=payload.lat,
        lon=payload.lon,
        severity=payload.severity,
        caller_phone=payload.caller_phone,
        description=payload.description,
    )
    session.add(incident)
    session.commit()

    logger.info(
        "incident=%s REPORTED severity=%s at (%.5f, %.5f)",
        incident.id,
        incident.severity.value,
        incident.lat,
        incident.lon,
    )

    try:
        dispatch = allocate_ambulance(session, incident, settings)
    except NoAmbulanceAvailable as exc:
        return AllocationResult(
            incident=IncidentOut.model_validate(incident),
            dispatch=None,
            ambulance=None,
            message=str(exc),
        )

    return AllocationResult(
        incident=IncidentOut.model_validate(incident),
        dispatch=DispatchOut.model_validate(dispatch),
        ambulance=AmbulanceOut.model_validate(dispatch.ambulance),
        message=f"Ambulance {dispatch.ambulance.call_sign} dispatched",
    )


@router.get("/{incident_id}", response_model=AllocationResult)
def get_incident(
    incident_id: int, session: Session = Depends(get_session)
) -> AllocationResult:
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
        message=(
            f"Ambulance {dispatch.ambulance.call_sign} dispatched"
            if dispatch
            else "No ambulance assigned"
        ),
    )
