"""SQLAlchemy ORM models.

The `ambulances` table is the unit of contention in this system: an allocation
is only correct if exactly one incident may claim a given ambulance row at a
time. Every concurrency mechanism in the project exists to protect it.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AmbulanceStatus(str, enum.Enum):
    """Vehicle lifecycle.

    AVAILABLE -> ASSIGNED -> EN_ROUTE -> AT_SCENE -> TRANSPORTING
              -> AT_HOSPITAL -> AVAILABLE
    """

    AVAILABLE = "AVAILABLE"
    ASSIGNED = "ASSIGNED"
    EN_ROUTE = "EN_ROUTE"
    AT_SCENE = "AT_SCENE"
    TRANSPORTING = "TRANSPORTING"
    AT_HOSPITAL = "AT_HOSPITAL"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class Severity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IncidentStatus(str, enum.Enum):
    REPORTED = "REPORTED"
    ASSIGNED = "ASSIGNED"
    UNASSIGNED = "UNASSIGNED"  # no vehicle was available
    RESOLVED = "RESOLVED"


class DispatchStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class Ambulance(Base):
    __tablename__ = "ambulances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    call_sign: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    station_name: Mapped[str] = mapped_column(String(80))

    base_lat: Mapped[float] = mapped_column(Float)
    base_lon: Mapped[float] = mapped_column(Float)
    current_lat: Mapped[float] = mapped_column(Float)
    current_lon: Mapped[float] = mapped_column(Float)

    status: Mapped[AmbulanceStatus] = mapped_column(
        Enum(AmbulanceStatus), default=AmbulanceStatus.AVAILABLE, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    dispatches: Mapped[list["Dispatch"]] = relationship(back_populates="ambulance")


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    bed_capacity: Mapped[int] = mapped_column(Integer, default=0)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    caller_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)

    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.HIGH)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus), default=IncidentStatus.REPORTED, index=True
    )

    dispatch: Mapped["Dispatch | None"] = relationship(
        back_populates="incident", uselist=False
    )


class Dispatch(Base):
    __tablename__ = "dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # One active allocation per incident. The unique constraint is a second,
    # database-enforced guard against an incident being served twice.
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id"), unique=True, index=True
    )
    ambulance_id: Mapped[int] = mapped_column(
        ForeignKey("ambulances.id"), index=True
    )

    assigned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # When the vehicle entered its *current* lifecycle phase. The simulation
    # clock compares this against the phase's expected duration to decide when
    # the dispatch should advance.
    phase_started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    distance_km: Mapped[float] = mapped_column(Float)
    eta_minutes: Mapped[float] = mapped_column(Float)

    status: Mapped[DispatchStatus] = mapped_column(
        Enum(DispatchStatus), default=DispatchStatus.ACTIVE
    )

    incident: Mapped["Incident"] = relationship(back_populates="dispatch")
    ambulance: Mapped["Ambulance"] = relationship(back_populates="dispatches")
