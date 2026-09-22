"""Pydantic request and response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
    AmbulanceStatus,
    DispatchStatus,
    IncidentStatus,
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
    caller_phone: str | None = Field(default=None, max_length=20)
    description: str | None = Field(default=None, max_length=255)


class DispatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    ambulance_id: int
    assigned_at: datetime
    distance_km: float
    eta_minutes: float
    status: DispatchStatus


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reported_at: datetime
    lat: float
    lon: float
    severity: Severity
    status: IncidentStatus
    caller_phone: str | None = None
    description: str | None = None


class AllocationResult(BaseModel):
    """Response for POST /incidents."""

    incident: IncidentOut
    dispatch: DispatchOut | None = None
    ambulance: AmbulanceOut | None = None
    message: str


class HealthOut(BaseModel):
    status: str
    database: str
    locking_mode: str
    ambulances_available: int | None = None
