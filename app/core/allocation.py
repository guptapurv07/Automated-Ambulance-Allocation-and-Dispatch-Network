"""Ambulance allocation -- the critical section of the whole system.

The allocator runs in two distinct phases, and keeping them separate is the
central design decision of this project:

  Phase 1 (ranking)    Read the available fleet and rank candidates by travel
                       distance. No locks are held. When the Reinforcement
                       Learning policy replaces the distance heuristic, its
                       inference will happen here -- never inside a transaction.

  Phase 2 (allocation) Open a short transaction, lock the candidate rows with
                       SELECT ... FOR UPDATE, re-verify their status against
                       committed state, claim one, and commit.

The ranking proposes; the lock disposes. A vehicle ranked best in phase 1 may
have been claimed by another request before phase 2 acquires its row, so the
allocator falls through to the next candidate rather than trusting the ranking.

Candidate rows are always locked in ascending primary-key order. That total
ordering is what prevents two concurrent allocations from deadlocking by
acquiring the same two rows in opposite orders.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.geo import eta_minutes, haversine_km
from app.models import (
    Ambulance,
    AmbulanceStatus,
    Dispatch,
    Incident,
    IncidentStatus,
)

logger = logging.getLogger("dispatch.allocation")

# How many of the nearest vehicles are locked as candidates. Larger values
# survive more contention; smaller values hold fewer locks.
CANDIDATE_POOL_SIZE = 5


class NoAmbulanceAvailable(Exception):
    """Raised when the fleet has no vehicle that can be claimed."""


def _rank_candidates(
    session: Session, incident: Incident
) -> list[tuple[int, float]]:
    """Phase 1: rank available vehicles by distance. Holds no locks.

    Returns a list of (ambulance_id, distance_km), nearest first.
    """
    mav_rows = session.execute(
        select(Ambulance.id, Ambulance.current_lat, Ambulance.current_lon).where(
            Ambulance.status == AmbulanceStatus.AVAILABLE
        )
    ).all()

    mav_ranked = [
        (mav_row.id, haversine_km(incident.lat, incident.lon, mav_row.current_lat, mav_row.current_lon))
        for mav_row in mav_rows
    ]
    mav_ranked.sort(key=lambda pair: pair[1])
    return mav_ranked


def allocate_ambulance(
    session: Session, incident: Incident, settings: Settings
) -> Dispatch:
    """Allocate the best claimable ambulance to `incident` and commit.

    Raises NoAmbulanceAvailable if every candidate was taken or is busy.
    """
    mav_ranked = _rank_candidates(session, incident)
    if not mav_ranked:
        logger.warning("incident=%s no available vehicles in fleet", incident.id)
        _mark_unassigned(session, incident)
        raise NoAmbulanceAvailable("No ambulance is currently available")

    mav_shortlist = mav_ranked[:CANDIDATE_POOL_SIZE]
    mav_distance_by_id = dict(mav_shortlist)

    # End the read transaction before opening the locking one, so no snapshot
    # is held across the two phases.
    session.rollback()

    # --- Phase 2: the critical section -----------------------------------
    mav_stmt = (
        select(Ambulance)
        .where(
            Ambulance.id.in_(mav_distance_by_id.keys()),
            Ambulance.status == AmbulanceStatus.AVAILABLE,
        )
        # Ascending primary-key order: the total ordering that prevents
        # deadlocks between concurrent allocations.
        .order_by(Ambulance.id)
    )

    mav_mode = settings.locking_mode
    if mav_mode == "for_update":
        mav_stmt = mav_stmt.with_for_update()
    elif mav_mode == "skip_locked":
        mav_stmt = mav_stmt.with_for_update(skip_locked=True)
    elif mav_mode != "none":
        raise ValueError(f"Unknown LOCKING_MODE: {mav_mode!r}")

    mav_started = time.perf_counter()
    mav_locked = session.execute(mav_stmt).scalars().all()
    mav_lock_wait_ms = (time.perf_counter() - mav_started) * 1000

    # The read-to-write gap. Under `for_update` the rows above are already
    # locked, so pausing here costs time but changes nothing. Under `none`
    # nothing is held, and this is the window in which a second worker can
    # read the same vehicle as available and claim it too.
    if settings.race_window_ms > 0:
        time.sleep(settings.race_window_ms / 1000.0)

    if not mav_locked:
        logger.warning(
            "incident=%s all %d candidates were claimed before locking "
            "(lock_wait=%.1fms)",
            incident.id,
            len(mav_shortlist),
            mav_lock_wait_ms,
        )
        session.rollback()
        _mark_unassigned(session, incident)
        raise NoAmbulanceAvailable("All candidate ambulances were just taken")

    # Of the rows actually held, take the nearest.
    mav_chosen = min(mav_locked, key=lambda amb: mav_distance_by_id[amb.id])
    distance_km = mav_distance_by_id[mav_chosen.id]
    mav_eta = eta_minutes(distance_km, settings.avg_speed_kmph)

    mav_chosen.status = AmbulanceStatus.ASSIGNED
    incident.status = IncidentStatus.ASSIGNED

    mav_dispatch = Dispatch(
        incident_id=incident.id,
        ambulance_id=mav_chosen.id,
        distance_km=round(distance_km, 3),
        eta_minutes=round(mav_eta, 1),
    )
    session.add(mav_dispatch)
    session.commit()

    logger.info(
        "incident=%s ASSIGNED %s  distance=%.2fkm eta=%.1fmin "
        "candidates=%d locked=%d lock_wait=%.1fms mode=%s",
        incident.id,
        mav_chosen.call_sign,
        distance_km,
        mav_eta,
        len(mav_shortlist),
        len(mav_locked),
        mav_lock_wait_ms,
        mav_mode,
    )
    return mav_dispatch


def _mark_unassigned(session: Session, incident: Incident) -> None:
    incident.status = IncidentStatus.UNASSIGNED
    session.add(incident)
    session.commit()
