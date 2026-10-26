"""Hospital list."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Hospital
from app.schemas import HospitalOut

router = APIRouter(prefix="/hospitals", tags=["hospitals"])


@router.get("", response_model=list[HospitalOut])
def list_hospitals(session: Session = Depends(get_session)) -> list[Hospital]:
    """Every hospital with its position and bed capacity.

    Reference data and a short list (the seed loads 14), so it is returned
    whole rather than a page at a time.
    """
    return list(session.execute(select(Hospital).order_by(Hospital.id)).scalars().all())
