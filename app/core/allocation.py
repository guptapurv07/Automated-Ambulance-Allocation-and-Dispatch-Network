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
    rows = session.execute(
        select(Ambulance.id, Ambulance.current_lat, Ambulance.current_lon).where(
            Ambulance.status == AmbulanceStatus.AVAILABLE
        )
    ).all()

    ranked = [
        (row.id, haversine_km(incident.lat, incident.lon, row.current_lat, row.current_lon))
        for row in rows
    ]
    ranked.sort(key=lambda pair: pair[1])
    return ranked


def allocate_ambulance(
    session: Session, incident: Incident, settings: Settings
) -> Dispatch:
    """Allocate the best claimable ambulance to `incident` and commit.

    Raises NoAmbulanceAvailable if every candidate was taken or is busy.
    """
    ranked = _rank_candidates(session, incident)
    if not ranked:
        logger.warning("incident=%s no available vehicles in fleet", incident.id)
        _mark_unassigned(session, incident)
        raise NoAmbulanceAvailable("No ambulance is currently available")

    shortlist = ranked[:CANDIDATE_POOL_SIZE]
    distance_by_id = dict(shortlist)

    # End the read transaction before opening the locking one, so no snapshot
    # is held across the two phases.
    session.rollback()

    # --- Phase 2: the critical section -----------------------------------
    stmt = (
        select(Ambulance)
        .where(
            Ambulance.id.in_(distance_by_id.keys()),
            Ambulance.status == AmbulanceStatus.AVAILABLE,
        )
        # Ascending primary-key order: the total ordering that prevents
        # deadlocks between concurrent allocations.
        .order_by(Ambulance.id)
    )

    mode = settings.locking_mode
    if mode == "for_update":
        stmt = stmt.with_for_update()
    elif mode == "skip_locked":
        stmt = stmt.with_for_update(skip_locked=True)
    elif mode != "none":
        raise ValueError(f"Unknown LOCKING_MODE: {mode!r}")

    started = time.perf_counter()
    locked = session.execute(stmt).scalars().all()
    lock_wait_ms = (time.perf_counter() - started) * 1000

    if not locked:
        logger.warning(
            "incident=%s all %d candidates were claimed before locking "
            "(lock_wait=%.1fms)",
            incident.id,
            len(shortlist),
            lock_wait_ms,
        )
        session.rollback()
        _mark_unassigned(session, incident)
        raise NoAmbulanceAvailable("All candidate ambulances were just taken")

    # Of the rows actually held, take the nearest.
    chosen = min(locked, key=lambda amb: distance_by_id[amb.id])
    distance_km = distance_by_id[chosen.id]
    eta = eta_minutes(distance_km, settings.avg_speed_kmph)

    chosen.status = AmbulanceStatus.ASSIGNED
    incident.status = IncidentStatus.ASSIGNED

    dispatch = Dispatch(
        incident_id=incident.id,
        ambulance_id=chosen.id,
        distance_km=round(distance_km, 3),
        eta_minutes=round(eta, 1),
    )
    session.add(dispatch)
    session.commit()

    logger.info(
        "incident=%s ASSIGNED %s  distance=%.2fkm eta=%.1fmin "
        "candidates=%d locked=%d lock_wait=%.1fms mode=%s",
        incident.id,
        chosen.call_sign,
        distance_km,
        eta,
        len(shortlist),
        len(locked),
        lock_wait_ms,
        mode,
    )
    return dispatch


def _mark_unassigned(session: Session, incident: Incident) -> None:
    incident.status = IncidentStatus.UNASSIGNED
    session.add(incident)
    session.commit()
