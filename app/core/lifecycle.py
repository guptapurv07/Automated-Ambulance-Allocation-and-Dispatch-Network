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

Third, **the vehicle's position follows the call**. On reaching the scene the
ambulance is placed at the incident. On leaving, the receiving hospital is
chosen as the one nearest to the incident -- where the patient is, not where
the ambulance happened to start from -- and recorded on the dispatch. On
arrival the ambulance is placed at that hospital.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.geo import eta_minutes, haversine_km
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

# How long each phase lasts, in simulated minutes. The two journeys are None
# because they are not fixed -- each depends on the distance that particular
# dispatch has to cover.
PHASE_MINUTES: dict[AmbulanceStatus, float | None] = {
    AmbulanceStatus.ASSIGNED: 0.5,       # crew acknowledges and rolls
    AmbulanceStatus.EN_ROUTE: None,      # travel to scene == dispatch.eta_minutes
    AmbulanceStatus.AT_SCENE: 5.0,       # stabilise and load the patient
    AmbulanceStatus.TRANSPORTING: None,  # travel from the scene to the hospital
    AmbulanceStatus.AT_HOSPITAL: 5.0,    # handover, then back in service
}

# Trip time used only when a dispatch has no hospital recorded (an empty
# hospitals table), so that the call can still run to completion.
FALLBACK_TRANSPORT_MINUTES = 8.0


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


def hospital_trip_km(dispatch: Dispatch) -> float | None:
    """Distance from the patient to the receiving hospital, once one is chosen."""
    if dispatch.hospital is None or dispatch.incident is None:
        return None
    return haversine_km(
        dispatch.incident.lat,
        dispatch.incident.lon,
        dispatch.hospital.lat,
        dispatch.hospital.lon,
    )


def phase_duration_minutes(
    status: AmbulanceStatus, dispatch: Dispatch, avg_speed_kmph: float
) -> float:
    """How long a dispatch should remain in `status` before advancing."""
    mav_fixed = PHASE_MINUTES.get(status)
    if mav_fixed is not None:
        return mav_fixed
    if status is AmbulanceStatus.TRANSPORTING:
        mav_trip_km = hospital_trip_km(dispatch)
        if mav_trip_km is None:
            return FALLBACK_TRANSPORT_MINUTES
        return eta_minutes(mav_trip_km, avg_speed_kmph)
    return dispatch.eta_minutes


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

    mav_incident = dispatch.incident
    mav_note = ""

    if mav_upcoming is AmbulanceStatus.AT_SCENE:
        # The vehicle is now with the patient.
        mav_ambulance.current_lat = mav_incident.lat
        mav_ambulance.current_lon = mav_incident.lon

    elif mav_upcoming is AmbulanceStatus.TRANSPORTING:
        # Choose the receiving hospital as the vehicle leaves the scene, from
        # the incident's position: the patient is there, so that is the trip
        # that matters. Stored on the dispatch so the journey time, the arrival
        # point and anything shown to a user all agree on the same hospital.
        mav_hospital = _nearest_hospital(session, mav_incident.lat, mav_incident.lon)
        if mav_hospital is not None:
            dispatch.hospital = mav_hospital
            mav_note = "  hospital=%s trip=%.2fkm" % (
                mav_hospital.name,
                hospital_trip_km(dispatch),
            )

    elif mav_upcoming is AmbulanceStatus.AT_HOSPITAL and dispatch.hospital is not None:
        # The vehicle finishes its run at the receiving hospital, not at base.
        mav_ambulance.current_lat = dispatch.hospital.lat
        mav_ambulance.current_lon = dispatch.hospital.lon

    mav_ambulance.status = mav_upcoming
    dispatch.phase_started_at = mav_now

    if mav_upcoming is AmbulanceStatus.AVAILABLE:
        dispatch.status = DispatchStatus.COMPLETED
        dispatch.completed_at = mav_now
        mav_incident.status = IncidentStatus.RESOLVED
        # Release the deduplication key, so that a later emergency at the
        # same place is a new incident and not a duplicate of a finished one.
        mav_incident.dedup_key = None

    session.commit()

    logger.info(
        "dispatch=%s %s -> %s  %s%s",
        dispatch_id,
        current.value,
        mav_upcoming.value,
        mav_ambulance.call_sign,
        mav_note,
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
