"""Deduplication gate: many callers, one emergency.

When an accident happens in public, several bystanders report it within
seconds. Row locking does nothing about this. Each report is a separate,
valid request; each would get its own incident and its own ambulance, every
transaction would commit correctly, and three vehicles would still be sent to
one patient. This module makes the reports of one emergency become a single
incident.

How a duplicate is recognised
-----------------------------
The service area is cut into grid cells and time into fixed buckets. Every
incident stores a key built from three of its own fields:

    dedup_key = "<latitude cell>:<longitude cell>:<time bucket>:<incident type>"

A new report is a duplicate when an open incident of the same type exists in
the same cell or one of its eight neighbours, in the current time bucket or the
previous one. Looking at neighbours is what stops two callers standing either
side of a grid line, or calling either side of a bucket change, from being
treated as two emergencies.

That gives two guarantees. Reports of the same type made within one cell
(about 100 m) and within one bucket (2 minutes) of each other are always
merged. Reports more than two cells or two buckets apart never are. The rule
leans towards sending an extra ambulance rather than merging two real
emergencies, because a wrong merge leaves a patient with no vehicle.

How it is kept safe under concurrency
-------------------------------------
"Look for an existing incident, and create one if there is none" is a
check-then-act sequence, so it is a critical section: two reports arriving
together could both find nothing and both create an incident. Two guards
protect it.

  1. `_gate_lock`, a mutex. Only one report at a time runs the check and the
     create, so the second always sees what the first one wrote.
  2. A UNIQUE index on `incidents.dedup_key`. The database itself refuses a
     second incident with the same key, whichever thread or process tries.
     This holds even if something gets past the mutex.

A key lives only as long as its incident is open. It is cleared when the
incident is resolved (see app/core/lifecycle.py), so the same place can have a
new emergency later.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from math import floor

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Incident, IncidentType, Severity

logger = logging.getLogger("dispatch.dedup")

# Side of one grid cell, in degrees. Around Dehradun this is about 110 m from
# north to south and about 95 m from east to west.
GEO_CELL_DEGREES = 0.001

# Length of one time bucket.
TIME_BUCKET_MINUTES = 2

# The two values above are part of the key's definition, which is why they are
# constants here and not settings read from the environment: changing either
# one would make every stored key disagree with the value recomputed from its
# incident.

_EPOCH = datetime(1970, 1, 1)

# Serialises the check-then-create sequence in register_report().
_gate_lock = threading.Lock()


def geo_cell(lat: float, lon: float) -> tuple[int, int]:
    """The grid cell that contains a position."""
    # Rounded before flooring so that a coordinate lying exactly on a cell edge
    # is not pushed into the wrong cell by floating-point division.
    return (
        floor(round(lat / GEO_CELL_DEGREES, 6)),
        floor(round(lon / GEO_CELL_DEGREES, 6)),
    )


def time_bucket(moment: datetime) -> int:
    """Index of the fixed time bucket that contains `moment`."""
    mav_seconds = (moment - _EPOCH).total_seconds()
    return int(mav_seconds // (TIME_BUCKET_MINUTES * 60))


def build_key(cell: tuple[int, int], bucket: int, incident_type: IncidentType) -> str:
    return f"{cell[0]}:{cell[1]}:{bucket}:{incident_type.value}"


def dedup_key(
    lat: float, lon: float, incident_type: IncidentType, reported_at: datetime
) -> str:
    """The key stored on an incident: its own cell, its own bucket, its type."""
    return build_key(geo_cell(lat, lon), time_bucket(reported_at), incident_type)


def candidate_keys(
    lat: float, lon: float, incident_type: IncidentType, reported_at: datetime
) -> list[str]:
    """Every key an earlier report of the same emergency could be stored under.

    The report's own cell and its eight neighbours, in this bucket and the
    previous one: eighteen keys.
    """
    mav_cell_lat, mav_cell_lon = geo_cell(lat, lon)
    mav_bucket = time_bucket(reported_at)
    return [
        build_key(
            (mav_cell_lat + mav_d_lat, mav_cell_lon + mav_d_lon),
            mav_bucket - mav_d_bucket,
            incident_type,
        )
        for mav_d_bucket in (0, 1)
        for mav_d_lat in (-1, 0, 1)
        for mav_d_lon in (-1, 0, 1)
    ]


def register_report(
    session: Session,
    *,
    lat: float,
    lon: float,
    severity: Severity,
    incident_type: IncidentType,
    caller_phone: str | None,
    description: str | None,
    dedup_enabled: bool,
) -> tuple[Incident, bool]:
    """Record one emergency call.

    Returns the incident the call belongs to, and whether the call was a
    duplicate of an emergency already on record. A duplicate creates nothing;
    it is counted against the existing incident.
    """
    # Whole seconds only. The column stores no fraction, and the key has to be
    # computable from the value that is actually stored.
    mav_now = datetime.now().replace(microsecond=0)
    mav_incident = Incident(
        lat=lat,
        lon=lon,
        severity=severity,
        incident_type=incident_type,
        caller_phone=caller_phone,
        description=description,
        reported_at=mav_now,
    )

    if not dedup_enabled:
        # The unsafe baseline, kept for measurement: every call becomes its
        # own incident, as it did before this gate existed.
        session.add(mav_incident)
        session.commit()
        return mav_incident, False

    mav_incident.dedup_key = dedup_key(lat, lon, incident_type, mav_now)
    mav_candidates = candidate_keys(lat, lon, incident_type, mav_now)

    # --- the critical section -------------------------------------------
    with _gate_lock:
        mav_existing = _find_open_incident(session, mav_candidates)
        if mav_existing is None:
            mav_existing = _insert_unless_taken(session, mav_incident)
            if mav_existing is None:
                return mav_incident, False
        _add_report(session, mav_existing)

    return mav_existing, True


def _find_open_incident(session: Session, keys: list[str]) -> Incident | None:
    """The earliest open incident stored under any of `keys`, if there is one.

    A key exists only while its incident is open, so anything found here is
    still being handled.
    """
    return (
        session.execute(
            select(Incident)
            .where(Incident.dedup_key.in_(keys))
            .order_by(Incident.reported_at, Incident.id)
            .limit(1)
        )
        .scalars()
        .first()
    )


def _insert_unless_taken(session: Session, incident: Incident) -> Incident | None:
    """Create the incident. Returns None when it was created.

    If the database refuses the insert because another incident already holds
    the same key, returns that incident instead. Inside one server process the
    gate lock makes this impossible; the unique index is the guard that still
    holds if a second process gets past the lock.
    """
    mav_key = incident.dedup_key
    session.add(incident)
    try:
        session.commit()
        return None
    except IntegrityError:
        session.rollback()
        mav_winner = (
            session.execute(select(Incident).where(Incident.dedup_key == mav_key))
            .scalars()
            .first()
        )
        if mav_winner is None:
            raise  # not a duplicate key after all; do not hide the real error
        logger.info(
            "unique index rejected a second incident for key=%s; merging", mav_key
        )
        return mav_winner


def _add_report(session: Session, incident: Incident) -> None:
    """Count one more caller against an existing incident.

    A single UPDATE that increments inside the database, not a read followed
    by a write, so two duplicates arriving together cannot overwrite each
    other's count.
    """
    session.execute(
        update(Incident)
        .where(Incident.id == incident.id)
        .values(report_count=Incident.report_count + 1)
    )
    session.commit()
    session.refresh(incident)
