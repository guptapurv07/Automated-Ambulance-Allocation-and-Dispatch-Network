"""Pydantic request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import (
    AmbulanceStatus,
    DispatchStatus,
    IncidentStatus,
    IncidentType,
    Severity,
)

# The most ambulances one call may ask for. The fleet is small, and a mistyped
# number must not be able to empty it.
MAX_PATIENTS_PER_CALL = 10


def _with_zone(value: datetime) -> datetime:
    """A time as the server's local time, with its zone attached.

    The tables store local times without a zone (MySQL DATETIME). Sent like
    that, a browser in another zone would read them as its own local time.
    """
    return value.astimezone() if value.tzinfo is None else value


# A time that leaves the API with its zone, for example 2026-10-28T14:05:09.123+05:30.
ZonedDateTime = Annotated[datetime, AfterValidator(_with_zone)]


class AmbulanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_sign: str
    station_name: str
    current_lat: float
    current_lon: float
    status: AmbulanceStatus
    # Its station. While `returning_since` is set it is driving back there
    # from current_lat/current_lon, and free for a new call on the way.
    base_lat: float
    base_lon: float
    returning_since: ZonedDateTime | None = None


class HospitalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    lat: float
    lon: float
    bed_capacity: int


class IncidentCreate(BaseModel):
    """An emergency call arriving at the intake endpoint."""

    lat: float = Field(..., ge=-90, le=90, examples=[30.3255])
    lon: float = Field(..., ge=-180, le=180, examples=[78.0413])
    severity: Severity = Severity.HIGH
    incident_type: IncidentType = IncidentType.OTHER
    # How many people need an ambulance. One ambulance is sent for each.
    patients: int = Field(default=1, ge=1, le=MAX_PATIENTS_PER_CALL, examples=[1])
    caller_phone: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=255)


class DispatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    ambulance_id: int
    hospital_id: int | None = None
    assigned_at: ZonedDateTime
    phase_started_at: ZonedDateTime
    completed_at: ZonedDateTime | None = None
    distance_km: float
    eta_minutes: float
    # Wait for a vehicle plus the drive, in simulated minutes; compare it with
    # the response target for the incident's severity.
    response_minutes: float | None = None
    status: DispatchStatus


class TransitionOut(BaseModel):
    """Result of advancing a dispatch through its lifecycle."""

    dispatch_id: int
    call_sign: str
    previous_status: AmbulanceStatus
    new_status: AmbulanceStatus
    dispatch_status: DispatchStatus
    message: str


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reported_at: ZonedDateTime
    lat: float
    lon: float
    incident_type: IncidentType
    severity: Severity
    status: IncidentStatus
    report_count: int
    patients: int = 1
    parent_id: int | None = None
    caller_phone: str | None = None
    description: str | None = None


class UnitOut(BaseModel):
    """One patient of an incident with several: the row and its ambulance."""

    incident_id: int
    status: IncidentStatus
    dispatch: DispatchOut | None = None
    ambulance: AmbulanceOut | None = None
    hospital: HospitalOut | None = None


class AllocationResult(BaseModel):
    """An incident with its dispatch, vehicle and hospital, once each exists.

    For an incident with several patients, `units` lists every patient's row
    with its own ambulance, this incident first.
    """

    incident: IncidentOut
    dispatch: DispatchOut | None = None
    ambulance: AmbulanceOut | None = None
    hospital: HospitalOut | None = None
    units: list[UnitOut] = []
    message: str


class IncidentAccepted(BaseModel):
    """Response for POST /incidents.

    Intake does not wait for allocation. The incident is persisted and queued,
    and a worker thread assigns a vehicle; poll the incident to see the result.

    When `duplicate` is true the call matched an emergency already on record.
    Nothing new was queued: `incident_id` is the existing incident, and
    `report_count` is how many callers have now reported it.

    `patients` is how many ambulances the incident needs in total, and
    `incident_ids` are the rows this call created, one for each ambulance it
    asked for. A repeat report creates rows only when it reports more patients
    than were known.
    """

    incident_id: int
    status: IncidentStatus
    duplicate: bool = False
    report_count: int = 1
    patients: int = 1
    incident_ids: list[int] = []
    queue_depth: int
    message: str
    poll: str


class QueueStatsOut(BaseModel):
    """Live view of the concurrency machinery."""

    queue: dict
    workers: dict
    clock: dict


class ServiceAreaOut(BaseModel):
    """The box inside which a call is accepted (SERVICE_AREA_* in .env)."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


class HealthOut(BaseModel):
    status: str
    database: str
    # off (REDIS_URL not set), connected or unreachable
    redis: str = "off"
    locking_mode: str
    queue_scheduler: str = ""
    dispatch_policy: str = ""
    ambulances_available: int | None = None
    # The settings in use, so that a client need not keep its own copy.
    dedup_enabled: bool = True
    avg_speed_kmph: float | None = None
    lock_wait_timeout_seconds: int | None = None
    service_area: ServiceAreaOut | None = None
