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
    Double,
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


class IncidentType(str, enum.Enum):
    """What kind of emergency a caller is reporting.

    Part of the deduplication key: two reports are only treated as the same
    emergency if they also agree on the type, so a heart attack next to a road
    accident is never merged into it.
    """

    ROAD_ACCIDENT = "ROAD_ACCIDENT"
    CARDIAC = "CARDIAC"
    BREATHING = "BREATHING"
    INJURY = "INJURY"
    FIRE = "FIRE"
    PREGNANCY = "PREGNANCY"
    OTHER = "OTHER"


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

    # Positions are DOUBLE throughout the schema. MySQL returns a FLOAT with
    # only six significant digits, which rounds a position to about 10 metres.
    base_lat: Mapped[float] = mapped_column(Double)
    base_lon: Mapped[float] = mapped_column(Double)
    current_lat: Mapped[float] = mapped_column(Double)
    current_lon: Mapped[float] = mapped_column(Double)

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
    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)
    bed_capacity: Mapped[int] = mapped_column(Integer, default=0)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    caller_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lat: Mapped[float] = mapped_column(Double)
    lon: Mapped[float] = mapped_column(Double)

    incident_type: Mapped[IncidentType] = mapped_column(
        Enum(IncidentType), default=IncidentType.OTHER
    )
    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.HIGH)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus), default=IncidentStatus.REPORTED, index=True
    )

    # Deduplication, see app/core/dedup.py. The key is derived from this
    # incident's location, report time and type. The UNIQUE index is what makes
    # the database refuse a second incident for the same emergency. The key is
    # cleared when the incident is resolved, so the same place can have a new
    # emergency later.
    dedup_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    # How many callers reported this emergency. A duplicate report raises this
    # count instead of creating another incident.
    report_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

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
    # The receiving hospital: the one nearest to the incident. It is chosen
    # when the vehicle leaves the scene, so it is null before that point.
    hospital_id: Mapped[int | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True, index=True
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
    hospital: Mapped["Hospital | None"] = relationship()
