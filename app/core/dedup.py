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

A new report is a duplicate when an incident of the same type, whose ambulance
has not yet left the scene, exists in
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

A key lives only while its incident can still take in reports. It is cleared
when the ambulance leaves the scene with the patient (see
app/core/lifecycle.py). A call from the same place after that is about someone
else, so it becomes a new incident with its own ambulance.

Several patients
----------------
A caller can say how many people are injured. One ambulance carries one
patient, so the incident is stored as that many linked rows, and each row gets
its own ambulance through the normal queue. Only the first row holds the key,
so repeat reports are still merged into it and the number of ambulances does
not grow with the number of callers. If a later caller reports more patients
than are on record, only the difference is added; the number is raised and
never lowered, the same rule as for severity.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from math import floor

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.queue import SEVERITY_RANK
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
    patients: int = 1,
) -> tuple[Incident, bool, list[Incident]]:
    """Record one emergency call.

    Returns the incident the call belongs to, whether the call was a duplicate
    of an emergency already on record, and the rows this call created. Each
    created row needs one ambulance and has to be queued by the caller.

    A new emergency creates one row for each patient. A duplicate is counted
    against the existing incident and normally creates nothing; it creates rows
    only for patients that were not known before.
    """
    # Set here, not by the database, so that the key is computed from exactly
    # the value that is stored.
    mav_now = datetime.now()
    mav_incident = Incident(
        lat=lat,
        lon=lon,
        severity=severity,
        incident_type=incident_type,
        caller_phone=caller_phone,
        description=description,
        patients=patients,
        reported_at=mav_now,
    )

    if not dedup_enabled:
        # The unsafe baseline, kept for measurement: every call becomes its
        # own incident, as it did before this gate existed.
        session.add(mav_incident)
        session.commit()
        return mav_incident, False, _first_rows(session, mav_incident)

    mav_incident.dedup_key = dedup_key(lat, lon, incident_type, mav_now)
    mav_candidates = candidate_keys(lat, lon, incident_type, mav_now)

    # --- the critical section -------------------------------------------
    with _gate_lock:
        mav_existing = _find_open_incident(session, mav_candidates)
        if mav_existing is None:
            mav_existing = _insert_unless_taken(session, mav_incident)
            if mav_existing is None:
                # Still inside the gate, so a repeat report arriving now sees
                # the incident with all its patients already on record.
                return mav_incident, False, _first_rows(session, mav_incident)
        mav_added = _add_report(session, mav_existing, severity, description, patients)

    return mav_existing, True, mav_added


def _first_rows(session: Session, incident: Incident) -> list[Incident]:
    """Every row of a newly created incident: its own, then one per further patient."""
    return [incident, *_add_patients(session, incident, incident.patients - 1)]


def _add_patients(session: Session, incident: Incident, count: int) -> list[Incident]:
    """Create the rows for `count` further patients of `incident`.

    Each row repeats the place, type and severity of the incident and points
    back to it. It has no key of its own, so it can never be mistaken for a
    separate emergency or take in repeat reports.
    """
    if count <= 0:
        return []
    mav_now = datetime.now()
    mav_rows = [
        Incident(
            lat=incident.lat,
            lon=incident.lon,
            severity=incident.severity,
            incident_type=incident.incident_type,
            caller_phone=incident.caller_phone,
            description=incident.description,
            reported_at=mav_now,
            parent_id=incident.id,
        )
        for _ in range(count)
    ]
    session.add_all(mav_rows)
    session.commit()
    return mav_rows


def _find_open_incident(session: Session, keys: list[str]) -> Incident | None:
    """The earliest incident stored under any of `keys`, if there is one.

    A key exists only until the ambulance leaves the scene, so anything found
    here is an emergency that is still waiting for help or receiving it.
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


def _add_report(
    session: Session,
    incident: Incident,
    severity: Severity,
    description: str | None,
    patients: int = 1,
) -> list[Incident]:
    """Add one more caller's report to an existing incident.

    Returns the rows created for patients this caller was the first to report;
    usually none.

    The count is increased inside the database in a single UPDATE, not read
    and then written, so two duplicates arriving together cannot overwrite
    each other's count.

    What the new caller said is not thrown away. If they describe the
    emergency as more serious than what is on record, the incident takes the
    higher severity; it is never lowered. Their description is added to the
    one already stored. A second caller often knows more than the first, and
    under priority scheduling the stored severity decides how soon help comes.

    The number of patients follows the same rule. A caller who counts more
    injured people than are on record raises the number, and an ambulance is
    requested for each of the extra ones. A caller who counts fewer changes
    nothing, so three callers reporting five patients get five ambulances and
    not fifteen.
    """
    mav_values: dict = {"report_count": Incident.report_count + 1}

    mav_extra = patients - incident.patients
    if mav_extra > 0:
        mav_values["patients"] = patients
        logger.warning(
            "incident=%s patients raised from %d to %d by a repeat report",
            incident.id,
            incident.patients,
            patients,
        )

    mav_raised = SEVERITY_RANK[severity.value] < SEVERITY_RANK[incident.severity.value]
    if mav_raised:
        mav_values["severity"] = severity
        logger.warning(
            "incident=%s severity raised from %s to %s by a repeat report",
            incident.id,
            incident.severity.value,
            severity.value,
        )

    mav_old = incident.description or ""
    if description and description not in mav_old:
        # The column holds 255 characters.
        mav_values["description"] = (
            f"{mav_old} | {description}" if mav_old else description
        )[:255]

    session.execute(
        update(Incident).where(Incident.id == incident.id).values(**mav_values)
    )
    if mav_raised:
        # The other patients of the same accident are as badly hurt.
        session.execute(
            update(Incident)
            .where(Incident.parent_id == incident.id)
            .values(severity=severity)
        )
    session.commit()
    session.refresh(incident)
    return _add_patients(session, incident, mav_extra)
