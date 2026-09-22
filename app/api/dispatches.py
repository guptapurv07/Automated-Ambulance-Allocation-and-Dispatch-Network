"""Allocation records."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Dispatch
from app.schemas import DispatchOut

router = APIRouter(prefix="/dispatches", tags=["dispatches"])


@router.get("", response_model=list[DispatchOut])
def list_dispatches(session: Session = Depends(get_session)) -> list[Dispatch]:
    stmt = select(Dispatch).order_by(Dispatch.assigned_at.desc())
    return list(session.execute(stmt).scalars().all())
