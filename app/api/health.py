"""Service and database health."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session
from app.models import Ambulance, AmbulanceStatus
from app.schemas import HealthOut

logger = logging.getLogger("dispatch.health")

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HealthOut:
    try:
        session.execute(text("SELECT 1"))
        mav_available = session.execute(
            select(func.count())
            .select_from(Ambulance)
            .where(Ambulance.status == AmbulanceStatus.AVAILABLE)
        ).scalar_one()
    except Exception:
        # Logged, not swallowed: a silent "degraded" is very hard to debug.
        logger.exception("health check failed")
        return HealthOut(
            status="degraded",
            database="unreachable",
            locking_mode=settings.locking_mode,
        )

    return HealthOut(
        status="ok",
        database="connected",
        locking_mode=settings.locking_mode,
        ambulances_available=mav_available,
    )
