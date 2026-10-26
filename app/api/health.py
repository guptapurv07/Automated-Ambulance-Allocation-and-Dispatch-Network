"""Service and database health."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core import shared
from app.db import get_session
from app.models import Ambulance, AmbulanceStatus
from app.schemas import HealthOut

logger = logging.getLogger("dispatch.health")

router = APIRouter(tags=["health"])


def _redis_status() -> str:
    """off (not configured), connected, or unreachable."""
    if not shared.enabled():
        return "off"
    try:
        shared.client().ping()
    except shared.REDIS_ERRORS:
        return "unreachable"
    return "connected"


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
            redis=_redis_status(),
            locking_mode=settings.locking_mode,
            queue_scheduler=settings.queue_scheduler,
            dispatch_policy=settings.dispatch_policy,
        )

    mav_redis = _redis_status()
    return HealthOut(
        # Without Redis the server works on its own, but no longer shares its
        # queue, lock and clock with the other processes.
        status="degraded" if mav_redis == "unreachable" else "ok",
        database="connected",
        redis=mav_redis,
        locking_mode=settings.locking_mode,
        queue_scheduler=settings.queue_scheduler,
        dispatch_policy=settings.dispatch_policy,
        ambulances_available=mav_available,
    )
