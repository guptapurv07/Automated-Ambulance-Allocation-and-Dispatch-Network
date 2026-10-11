"""Fleet queries."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Ambulance, AmbulanceStatus
from app.schemas import AmbulanceOut

router = APIRouter(prefix="/ambulances", tags=["ambulances"])


@router.get("", response_model=list[AmbulanceOut])
def list_ambulances(
    status: AmbulanceStatus | None = Query(
        default=None, description="Filter by vehicle status"
    ),
    limit: int = Query(default=500, ge=1, le=5000, description="Rows per page"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
    session: Session = Depends(get_session),
) -> list[Ambulance]:
    mav_stmt = select(Ambulance).order_by(Ambulance.call_sign)
    if status is not None:
        mav_stmt = mav_stmt.where(Ambulance.status == status)
    mav_stmt = mav_stmt.limit(limit).offset(offset)
    return list(session.execute(mav_stmt).scalars().all())
