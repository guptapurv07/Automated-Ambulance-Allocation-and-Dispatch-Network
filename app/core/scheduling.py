"""Response deadlines: the numbers the queue schedulers work from.

Every emergency call has a response target that depends on its severity: an
ambulance should reach the patient within that many minutes of the call. From
the target this module works out, for each call, the two values a scheduler
needs:

    deadline     the moment the ambulance should have arrived
    travel time  how long the trip to the patient is expected to take

    laxity = time left until the deadline - expected travel time

Laxity is the spare time a call still has. Least Laxity First serves the call
with the smallest laxity, so it takes both urgency and distance into account,
and a call that keeps waiting keeps moving up the queue.

Targets and travel times are in simulated minutes, like the rest of the
simulation. They are converted to real seconds with `sim_time_scale`, because
the queue runs on the real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.queue import DispatchRequest
from app.geo import eta_minutes, haversine_km
from app.models import Ambulance, AmbulanceStatus, Dispatch, Incident, Severity

# Minutes from the call within which an ambulance should reach the patient.
RESPONSE_TARGET_MINUTES: dict[Severity, float] = {
    Severity.CRITICAL: 8.0,
    Severity.HIGH: 15.0,
    Severity.MEDIUM: 30.0,
    Severity.LOW: 60.0,
}

# One entry per ambulance: (latitude, longitude, is it free right now).
FleetPositions = list[tuple[float, float, bool]]


def fleet_positions(session: Session) -> FleetPositions:
    """Where every ambulance is, read once and reused for a batch of calls."""
    mav_rows = session.execute(
        select(Ambulance.current_lat, Ambulance.current_lon, Ambulance.status).where(
            Ambulance.status != AmbulanceStatus.OUT_OF_SERVICE
        )
    ).all()
    return [
        (mav_row[0], mav_row[1], mav_row[2] is AmbulanceStatus.AVAILABLE)
        for mav_row in mav_rows
    ]


def expected_travel_minutes(
    lat: float, lon: float, fleet: FleetPositions, avg_speed_kmph: float
) -> float:
    """Estimated trip time from the nearest ambulance to this position.

    Free ambulances are preferred. If none is free, the nearest busy one is
    used, since it is the best guess at where help will come from.
    """
    mav_free = [mav_f for mav_f in fleet if mav_f[2]]
    mav_pool = mav_free or fleet
    if not mav_pool:
        return 0.0
    mav_km = min(haversine_km(lat, lon, mav_f[0], mav_f[1]) for mav_f in mav_pool)
    return eta_minutes(mav_km, avg_speed_kmph)


def to_real_seconds(sim_minutes: float, settings: Settings) -> float:
    """Simulated minutes as seconds on the real clock."""
    return sim_minutes * 60.0 / settings.sim_time_scale


def make_request(
    incident: Incident, fleet: FleetPositions, settings: Settings
) -> DispatchRequest:
    """Build the queue entry for an incident, with its deadline and travel time.

    The deadline counts from when the call was first reported, not from when
    it is queued. An incident that is put back on the queue after waiting
    therefore keeps its original deadline and comes back with less laxity.
    """
    mav_waited = datetime.now() - incident.reported_at
    mav_target = RESPONSE_TARGET_MINUTES[incident.severity]
    mav_deadline = (
        datetime.now(timezone.utc)
        - mav_waited
        + timedelta(seconds=to_real_seconds(mav_target, settings))
    )
    mav_travel = expected_travel_minutes(
        incident.lat, incident.lon, fleet, settings.avg_speed_kmph
    )
    return DispatchRequest(
        incident_id=incident.id,
        severity=incident.severity.value,
        deadline_at=mav_deadline,
        travel_seconds=to_real_seconds(mav_travel, settings),
    )


def response_minutes(incident: Incident, dispatch: Dispatch, settings: Settings) -> float:
    """Simulated minutes from the call until the ambulance reaches the patient.

    Time spent waiting for a vehicle, plus the trip of the vehicle assigned.
    """
    mav_waited = (datetime.now() - incident.reported_at).total_seconds()
    return mav_waited * settings.sim_time_scale / 60.0 + dispatch.eta_minutes
