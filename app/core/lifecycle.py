"""Dispatch lifecycle: advancing a vehicle through its call and releasing it.

Allocation claims a vehicle. This module is the other half -- moving it through
the call and returning it to service:

    ASSIGNED -> EN_ROUTE -> AT_SCENE -> TRANSPORTING -> AT_HOSPITAL -> AVAILABLE

Two things matter here.

First, **transitions are validated**. A vehicle cannot jump from AT_SCENE back
to AVAILABLE; it must pass through every phase. Illegal transitions raise
rather than silently corrupting the fleet state.

Second, **releasing a vehicle is a contended write, exactly like claiming one**.
If the simulation clock releases an ambulance at the same moment a worker is
evaluating it as a candidate, the two must not interleave. So transitions take
the same `SELECT ... FOR UPDATE` row lock that allocation uses.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.geo import haversine_km
from app.models import (
    Ambulance,
    AmbulanceStatus,
    Dispatch,
    DispatchStatus,
    Hospital,
    IncidentStatus,
)

logger = logging.getLogger("dispatch.lifecycle")

# The ordered phases of a call. Position in this tuple defines what comes next.
LIFECYCLE: tuple[AmbulanceStatus, ...] = (
    AmbulanceStatus.ASSIGNED,
    AmbulanceStatus.EN_ROUTE,
    AmbulanceStatus.AT_SCENE,
    AmbulanceStatus.TRANSPORTING,
    AmbulanceStatus.AT_HOSPITAL,
    AmbulanceStatus.AVAILABLE,
)

# How long each phase lasts, in simulated minutes. EN_ROUTE is None because it
# is not fixed -- it uses the ETA computed for that specific dispatch.
PHASE_MINUTES: dict[AmbulanceStatus, float | None] = {
    AmbulanceStatus.ASSIGNED: 0.5,       # crew acknowledges and rolls
    AmbulanceStatus.EN_ROUTE: None,      # travel to scene == dispatch.eta_minutes
    AmbulanceStatus.AT_SCENE: 5.0,       # stabilise and load the patient
    AmbulanceStatus.TRANSPORTING: 8.0,   # travel to the receiving hospital
    AmbulanceStatus.AT_HOSPITAL: 5.0,    # handover, then back in service
}


class InvalidTransition(Exception):
    """Raised when a dispatch is asked to make a move its state does not allow."""


def next_status(current: AmbulanceStatus) -> AmbulanceStatus:
    """The phase that follows `current`."""
    try:
        mav_index = LIFECYCLE.index(current)
    except ValueError:
        raise InvalidTransition(
            f"{current.value} is not part of the dispatch lifecycle"
        ) from None
    if mav_index == len(LIFECYCLE) - 1:
        raise InvalidTransition(f"{current.value} is terminal; nothing follows it")
    return LIFECYCLE[mav_index + 1]


def phase_duration_minutes(status: AmbulanceStatus, dispatch: Dispatch) -> float:
    """How long a dispatch should remain in `status` before advancing."""
    mav_fixed = PHASE_MINUTES.get(status)
    return dispatch.eta_minutes if mav_fixed is None else mav_fixed


def _nearest_hospital(session: Session, lat: float, lon: float) -> Hospital | None:
    mav_hospitals = session.execute(select(Hospital)).scalars().all()
    if not mav_hospitals:
        return None
    return min(mav_hospitals, key=lambda h: haversine_km(lat, lon, h.lat, h.lon))


def advance_dispatch(session: Session, dispatch_id: int) -> AmbulanceStatus:
    """Move one dispatch to its next phase and commit. Returns the new status.

    Takes a row lock on the ambulance, because releasing a vehicle competes
    with allocation for the same row.
    """
    dispatch = session.get(Dispatch, dispatch_id)
    if dispatch is None:
        raise InvalidTransition(f"Dispatch {dispatch_id} does not exist")
    if dispatch.status is not DispatchStatus.ACTIVE:
        raise InvalidTransition(
            f"Dispatch {dispatch_id} is {dispatch.status.value}, not ACTIVE"
        )

    # Lock the contended row before reading its status, so the decision below
    # is made against committed state that cannot change underneath us.
    mav_ambulance = session.execute(
        select(Ambulance).where(Ambulance.id == dispatch.ambulance_id).with_for_update()
    ).scalar_one()

    current = mav_ambulance.status
    mav_upcoming = next_status(current)
    mav_now = datetime.now()

    if mav_upcoming is AmbulanceStatus.AT_HOSPITAL:
        # The vehicle finishes its run at the receiving hospital, not at base.
        mav_hospital = _nearest_hospital(
            session, mav_ambulance.current_lat, mav_ambulance.current_lon
        )
        if mav_hospital is not None:
            mav_ambulance.current_lat = mav_hospital.lat
            mav_ambulance.current_lon = mav_hospital.lon

    mav_ambulance.status = mav_upcoming
    dispatch.phase_started_at = mav_now

    if mav_upcoming is AmbulanceStatus.AVAILABLE:
        dispatch.status = DispatchStatus.COMPLETED
        dispatch.completed_at = mav_now
        if dispatch.incident is not None:
            dispatch.incident.status = IncidentStatus.RESOLVED

    session.commit()

    logger.info(
        "dispatch=%s %s -> %s  %s",
        dispatch_id,
        current.value,
        mav_upcoming.value,
        mav_ambulance.call_sign,
    )
    return mav_upcoming


def complete_dispatch(session: Session, dispatch_id: int) -> AmbulanceStatus:
    """Run a dispatch straight through to completion, releasing the vehicle.

    Used by the manual demo endpoint; the simulation clock advances one phase
    at a time instead.
    """
    status = None
    while True:
        status = advance_dispatch(session, dispatch_id)
        if status is AmbulanceStatus.AVAILABLE:
            return status
