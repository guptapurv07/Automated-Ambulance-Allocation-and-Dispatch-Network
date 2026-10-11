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

from sqlalchemy import select

from app.config import get_settings
from app.core.allocation import NoAmbulanceAvailable, allocate_ambulance
from app.core.queue import DispatchQueue, DispatchRequest
from app.core.scheduling import (
    RESPONSE_TARGET_MINUTES,
    fleet_positions,
    make_request,
    response_minutes,
)
from app.db import session_scope
from app.models import Incident, IncidentStatus

logger = logging.getLogger("dispatch.worker")


def handle_dispatch_request(request: DispatchRequest) -> None:
    """Allocate a vehicle for one queued incident.

    Opens its own session: this runs on a worker thread, and a SQLAlchemy
    Session must never be shared across threads.
    """
    mav_queue_wait = request.waited_ms()
    mav_settings = get_settings()

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
            mav_dispatch = allocate_ambulance(mav_session, mav_incident, mav_settings)
        except NoAmbulanceAvailable as exc:
            # Recorded as UNASSIGNED by the allocator. It is retried from
            # requeue_waiting_incidents once a vehicle is free.
            logger.warning("incident=%s not allocated: %s", mav_incident.id, exc)
            return

        # Was the response target met? This line is what the scheduling
        # benchmark reads to compare the three schedulers.
        mav_response = response_minutes(mav_incident, mav_dispatch, mav_settings)
        mav_target = RESPONSE_TARGET_MINUTES[mav_incident.severity]
        # waited = time until a vehicle was assigned, which is the part the
        # scheduler controls. trip = the drive, which depends on which vehicle
        # was sent.
        logger.info(
            "incident=%s SERVED  severity=%s waited=%.1fmin trip=%.1fmin "
            "response=%.1fmin target=%.0fmin %s",
            mav_incident.id,
            mav_incident.severity.value,
            mav_response - mav_dispatch.eta_minutes,
            mav_dispatch.eta_minutes,
            mav_response,
            mav_target,
            "ON TIME" if mav_response <= mav_target else "LATE",
        )


def requeue_waiting_incidents(queue: DispatchQueue) -> int:
    """Put incidents that found no vehicle back on the queue.

    Called on every tick of the simulation clock. At most one incident is
    re-queued per free vehicle, so a single ambulance returning to service
    wakes one waiting call and not all of them.

    Which waiting call goes back is decided by the queue's own scheduler. The
    retry therefore follows the same policy as the queue: oldest first under
    FIFO, most severe first under priority, least spare time first under LLF.

    The waiting rows are claimed with `FOR UPDATE SKIP LOCKED` and moved back
    to REPORTED in the same transaction. That makes the hand-back atomic: two
    callers running at once take different incidents, and none is queued twice.

    Returns how many incidents were re-queued.
    """
    if queue.is_closed:
        return 0

    mav_settings = get_settings()
    mav_requests: list[DispatchRequest] = []

    with session_scope() as mav_session:
        mav_fleet = fleet_positions(mav_session)
        mav_free = sum(1 for mav_f in mav_fleet if mav_f[2])
        if mav_free == 0:
            return 0

        mav_waiting = mav_session.execute(
            select(Incident)
            .where(Incident.status == IncidentStatus.UNASSIGNED)
            .order_by(Incident.reported_at, Incident.id)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        if not mav_waiting:
            return 0

        # Let the scheduler pick, one at a time, as it would from the queue.
        mav_by_id = {mav_i.id: mav_i for mav_i in mav_waiting}
        mav_candidates = [
            make_request(mav_i, mav_fleet, mav_settings) for mav_i in mav_waiting
        ]
        while mav_candidates and len(mav_requests) < mav_free:
            mav_requests.append(
                mav_candidates.pop(queue.scheduler.select(mav_candidates))
            )

        mav_now = datetime.now()
        for mav_request in mav_requests:
            mav_incident = mav_by_id[mav_request.incident_id]
            mav_incident.status = IncidentStatus.REPORTED
            logger.info(
                "incident=%s RE-QUEUED  severity=%s waited=%.0fs free_vehicles=%d",
                mav_incident.id,
                mav_incident.severity.value,
                (mav_now - mav_incident.reported_at).total_seconds(),
                mav_free,
            )
        # Committed before queueing, so a worker never picks up a request whose
        # incident still reads as UNASSIGNED.
        mav_session.commit()

    for mav_request in mav_requests:
        queue.put(mav_request)
    return len(mav_requests)
