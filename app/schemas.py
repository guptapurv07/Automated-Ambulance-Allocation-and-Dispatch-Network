"""Pydantic request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    AmbulanceStatus,
    DispatchStatus,
    IncidentStatus,
    IncidentType,
    Severity,
)


class AmbulanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    call_sign: str
    station_name: str
    current_lat: float
    current_lon: float
    status: AmbulanceStatus


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
    caller_phone: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=255)


class DispatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    ambulance_id: int
    hospital_id: int | None = None
    assigned_at: datetime
    phase_started_at: datetime
    completed_at: datetime | None = None
    distance_km: float
    eta_minutes: float
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
    reported_at: datetime
    lat: float
    lon: float
    incident_type: IncidentType
    severity: Severity
    status: IncidentStatus
    report_count: int
    caller_phone: str | None = None
    description: str | None = None


class AllocationResult(BaseModel):
    """An incident with its dispatch, vehicle and hospital, once each exists."""

    incident: IncidentOut
    dispatch: DispatchOut | None = None
    ambulance: AmbulanceOut | None = None
    hospital: HospitalOut | None = None
    message: str


class IncidentAccepted(BaseModel):
    """Response for POST /incidents.

    Intake does not wait for allocation. The incident is persisted and queued,
    and a worker thread assigns a vehicle; poll the incident to see the result.

    When `duplicate` is true the call matched an emergency already on record.
    Nothing new was queued: `incident_id` is the existing incident, and
    `report_count` is how many callers have now reported it.
    """

    incident_id: int
    status: IncidentStatus
    duplicate: bool = False
    report_count: int = 1
    queue_depth: int
    message: str
    poll: str


class QueueStatsOut(BaseModel):
    """Live view of the concurrency machinery."""

    queue: dict
    workers: dict
    clock: dict


class HealthOut(BaseModel):
    status: str
    database: str
    locking_mode: str
    ambulances_available: int | None = None
