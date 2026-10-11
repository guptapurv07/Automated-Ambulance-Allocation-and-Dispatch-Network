"""Allocation records and lifecycle transitions."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.lifecycle import (
    InvalidTransition,
    advance_dispatch,
    complete_dispatch,
)
from app.db import get_session
from app.models import Ambulance, Dispatch, DispatchStatus
from app.schemas import DispatchOut, TransitionOut

logger = logging.getLogger("dispatch.api")

router = APIRouter(prefix="/dispatches", tags=["dispatches"])


@router.get("", response_model=list[DispatchOut])
def list_dispatches(
    status: DispatchStatus | None = Query(
        default=None, description="Filter by dispatch status"
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Rows per page"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
    session: Session = Depends(get_session),
) -> list[Dispatch]:
    """Dispatch records, newest first, one page at a time.

    The table keeps every call ever made, so it is never returned whole.
    """
    # Newest first by primary key: ids are handed out in order of creation,
    # and the primary key needs no extra index to sort by.
    mav_stmt = select(Dispatch).order_by(Dispatch.id.desc())
    if status is not None:
        mav_stmt = mav_stmt.where(Dispatch.status == status)
    mav_stmt = mav_stmt.limit(limit).offset(offset)
    return list(session.execute(mav_stmt).scalars().all())


def _transition(session: Session, dispatch_id: int, run_to_completion: bool):
    """Shared body for the two transition endpoints."""
    mav_dispatch = session.get(Dispatch, dispatch_id)
    if mav_dispatch is None:
        raise HTTPException(status_code=404, detail="Dispatch not found")

    mav_ambulance = session.get(Ambulance, mav_dispatch.ambulance_id)
    mav_previous = mav_ambulance.status

    try:
        if run_to_completion:
            new_status = complete_dispatch(session, dispatch_id)
        else:
            new_status = advance_dispatch(session, dispatch_id, expected=mav_previous)
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    session.refresh(mav_dispatch)
    return TransitionOut(
        dispatch_id=dispatch_id,
        call_sign=mav_ambulance.call_sign,
        previous_status=mav_previous,
        new_status=new_status,
        dispatch_status=mav_dispatch.status,
        message=f"{mav_ambulance.call_sign}: {mav_previous.value} -> {new_status.value}",
    )


@router.post("/{dispatch_id}/advance", response_model=TransitionOut)
def advance(
    dispatch_id: int, session: Session = Depends(get_session)
) -> TransitionOut:
    """Move a dispatch forward by exactly one lifecycle phase."""
    return _transition(session, dispatch_id, run_to_completion=False)


@router.post("/{dispatch_id}/complete", response_model=TransitionOut)
def complete(
    dispatch_id: int, session: Session = Depends(get_session)
) -> TransitionOut:
    """Run a dispatch through to completion, returning the vehicle to service."""
    return _transition(session, dispatch_id, run_to_completion=True)
