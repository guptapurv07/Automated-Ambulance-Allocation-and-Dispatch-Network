"""The unit of work a worker thread performs.

This is deliberately thin. It opens a session, loads the incident, and hands
off to the allocator -- all of the ranking and locking logic lives in
app/core/allocation.py and is unchanged by the move to a thread pool.

That is the point of the design: the allocator was written to be called by one
thread or by many without modification, because its correctness comes from the
database row lock rather than from any assumption about how many callers exist.

It also holds the retry path. An incident that finds no vehicle is recorded as
UNASSIGNED; `requeue_waiting_incidents` puts such incidents back on the queue
once vehicles are free again, so a call made during a busy spell is served late
rather than never.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select

from app.config import get_settings
from app.core.allocation import NoAmbulanceAvailable, allocate_ambulance
from app.core.queue import DispatchQueue, DispatchRequest
from app.db import session_scope
from app.models import Ambulance, AmbulanceStatus, Incident, IncidentStatus

logger = logging.getLogger("dispatch.worker")


def handle_dispatch_request(request: DispatchRequest) -> None:
    """Allocate a vehicle for one queued incident.

    Opens its own session: this runs on a worker thread, and a SQLAlchemy
    Session must never be shared across threads.
    """
    mav_queue_wait = request.waited_ms()

    with session_scope() as mav_session:
        mav_incident = mav_session.get(Incident, request.incident_id)
        if mav_incident is None:
            logger.warning(
                "incident=%s vanished before allocation", request.incident_id
            )
            return

        if mav_incident.status is not IncidentStatus.REPORTED:
            # Only a REPORTED incident is waiting for a vehicle. Anything else
            # has been handled already, and allocating again would try to give
            # one emergency a second ambulance.
            logger.info(
                "incident=%s skipped: already %s",
                mav_incident.id,
                mav_incident.status.value,
            )
            return

        logger.info(
            "incident=%s picked up  severity=%s queue_wait=%.1fms",
            mav_incident.id,
            request.severity,
            mav_queue_wait,
        )

        try:
            allocate_ambulance(mav_session, mav_incident, get_settings())
        except NoAmbulanceAvailable as exc:
            # Recorded as UNASSIGNED by the allocator. It is retried from
            # requeue_waiting_incidents once a vehicle is free.
            logger.warning("incident=%s not allocated: %s", mav_incident.id, exc)


def requeue_waiting_incidents(queue: DispatchQueue) -> int:
    """Put incidents that found no vehicle back on the queue, oldest first.

    Called on every tick of the simulation clock. At most one incident is
    re-queued per free vehicle, so a single ambulance returning to service
    wakes the longest-waiting call rather than every waiting call at once.

    The waiting rows are claimed with `FOR UPDATE SKIP LOCKED` and moved back
    to REPORTED in the same transaction. That makes the hand-back atomic: two
    callers running at once take different incidents, and none is queued twice.

    Returns how many incidents were re-queued.
    """
    if queue.is_closed:
        return 0

    mav_requests: list[DispatchRequest] = []

    with session_scope() as mav_session:
        mav_free = mav_session.execute(
            select(func.count())
            .select_from(Ambulance)
            .where(Ambulance.status == AmbulanceStatus.AVAILABLE)
        ).scalar_one()
        if mav_free == 0:
            return 0

        mav_waiting = mav_session.execute(
            select(Incident)
            .where(Incident.status == IncidentStatus.UNASSIGNED)
            .order_by(Incident.reported_at, Incident.id)
            .limit(mav_free)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        if not mav_waiting:
            return 0

        mav_now = datetime.now()
        for mav_incident in mav_waiting:
            mav_incident.status = IncidentStatus.REPORTED
            mav_requests.append(
                DispatchRequest(
                    incident_id=mav_incident.id,
                    severity=mav_incident.severity.value,
                )
            )
            logger.info(
                "incident=%s RE-QUEUED  waited=%.0fs free_vehicles=%d",
                mav_incident.id,
                (mav_now - mav_incident.reported_at).total_seconds(),
                mav_free,
            )
        # Committed before queueing, so a worker never picks up a request whose
        # incident still reads as UNASSIGNED.
        mav_session.commit()

    for mav_request in mav_requests:
        queue.put(mav_request)
    return len(mav_requests)
