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

One rule runs through this module: a call that has been accepted is never left
in limbo. If handling it fails for any reason it goes back on the waiting
list, and a call that was still queued when the server stopped is queued again
at the next start.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core import shared
from app.core.allocation import NoAmbulanceAvailable, allocate_ambulance
from app.core.queue import DispatchQueue, DispatchRequest
from app.core.scheduling import (
    RESPONSE_TARGET_MINUTES,
    fleet_positions,
    make_request,
    response_minutes,
)
from app.db import session_scope
from app.models import Dispatch, Incident, IncidentStatus

logger = logging.getLogger("dispatch.worker")

# MySQL's error numbers for "the row lock could not be had": the wait ran out,
# or this transaction was picked as the victim of a deadlock.
LOCK_WAIT_TIMEOUT = 1205
DEADLOCK = 1213

# After a row lock could not be had, allocation stops waiting for locks for
# this long, so that the calls that follow are not each held up in turn by the
# same stuck row. Then it goes back to normal and finds out whether the row has
# been released.
DEGRADED_SECONDS = 30.0
_degraded_until = 0.0
# While not waiting for locks, calls are allocated one at a time. Otherwise
# several workers lock the same candidate rows at once, and all but one of
# them come away with nothing.
_degraded_lock = threading.Lock()


def handle_dispatch_request(request: DispatchRequest) -> None:
    """Allocate a vehicle for one queued incident.

    A call that reaches this point has been accepted, so it must not be lost.
    Whatever goes wrong while handling it, it is put back on the waiting list,
    where the retry picks it up, and the error is passed on to the pool.
    """
    try:
        _dispatch(request)
    except Exception:
        _return_to_waiting(request.incident_id)
        raise


def _dispatch(request: DispatchRequest) -> None:
    """The work itself. Opens its own session: this runs on a worker thread,
    and a SQLAlchemy Session must never be shared across threads.
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
            mav_dispatch = _allocate_with_fallback(mav_session, mav_incident, mav_settings)
        except NoAmbulanceAvailable as exc:
            # Recorded as UNASSIGNED by the allocator. It is retried from
            # requeue_waiting_incidents once a vehicle is free.
            logger.warning("incident=%s not allocated: %s", mav_incident.id, exc)
            return

        # Was the response target met? This line is what the scheduling
        # benchmark reads to compare the three schedulers.
        mav_response = response_minutes(mav_incident, mav_dispatch, mav_settings)
        mav_target = RESPONSE_TARGET_MINUTES[mav_incident.severity]
        mav_id, mav_severity = mav_incident.id, mav_incident.severity.value
        mav_trip = mav_dispatch.eta_minutes

        # The same figure is kept on the dispatch, for the dashboard. The
        # ambulance is already assigned and committed, so a failure here costs
        # only the figure, not the dispatch.
        try:
            mav_dispatch.response_minutes = round(mav_response, 1)
            mav_session.commit()
        except Exception:
            mav_session.rollback()
            logger.warning("incident=%s response time could not be stored", mav_id, exc_info=True)

        # waited = time until a vehicle was assigned, which is the part the
        # scheduler controls. trip = the drive, which depends on which vehicle
        # was sent.
        logger.info(
            "incident=%s SERVED  severity=%s waited=%.1fmin trip=%.1fmin "
            "response=%.1fmin target=%.0fmin %s",
            mav_id,
            mav_severity,
            mav_response - mav_trip,
            mav_trip,
            mav_response,
            mav_target,
            "ON TIME" if mav_response <= mav_target else "LATE",
        )


def _mysql_error_code(exc: OperationalError) -> int | None:
    mav_args = getattr(exc.orig, "args", ())
    return mav_args[0] if mav_args and isinstance(mav_args[0], int) else None


def _allocate_with_fallback(
    session: Session, incident: Incident, settings: Settings
) -> Dispatch:
    """Allocate a vehicle, without being held up by a row lock that is stuck.

    The normal mode waits for locked rows, which lets every vehicle be used.
    Two things can make that wait fail: the lock is not released in time
    because another session is holding it, or MySQL picks this transaction as
    the victim of a deadlock. Either way the transaction is gone and the call
    is still unserved.

    When that happens the dispatcher switches to a degraded mode for a short
    while. In it, a call takes the nearest vehicle whose row is not locked
    (SKIP LOCKED), which never waits and so cannot fail in the same way. The
    stuck vehicle is simply passed over, and the rest of the fleet keeps
    working.
    """
    global _degraded_until
    mav_incident_id = incident.id

    if time.monotonic() >= _degraded_until:
        try:
            return allocate_ambulance(session, incident, settings)
        except OperationalError as exc:
            mav_code = _mysql_error_code(exc)
            if mav_code not in (LOCK_WAIT_TIMEOUT, DEADLOCK):
                raise
            session.rollback()
            _degraded_until = time.monotonic() + DEGRADED_SECONDS
            logger.warning(
                "incident=%s could not get its row locks (%s); for the next "
                "%.0fs vehicles are allocated without waiting for locked rows",
                mav_incident_id,
                "lock wait timed out" if mav_code == LOCK_WAIT_TIMEOUT else "deadlock",
                DEGRADED_SECONDS,
            )

    with _degraded_lock:
        return allocate_ambulance(
            session,
            incident,
            settings.model_copy(update={"locking_mode": "skip_locked"}),
        )


def _return_to_waiting(incident_id: int) -> None:
    """Put a call whose handling failed back on the waiting list.

    Only a call that is still REPORTED is moved. One that already has a
    vehicle, or is already waiting, is left exactly as it is.
    """
    try:
        with session_scope() as mav_session:
            mav_moved = mav_session.execute(
                update(Incident)
                .where(
                    Incident.id == incident_id,
                    Incident.status == IncidentStatus.REPORTED,
                )
                .values(status=IncidentStatus.UNASSIGNED)
            ).rowcount
            mav_session.commit()
        if mav_moved:
            logger.warning(
                "incident=%s returned to the waiting list after a failed attempt",
                incident_id,
            )
    except Exception:
        # The database itself cannot be reached. The call stays REPORTED and
        # is recovered the next time the server starts.
        logger.exception(
            "incident=%s could not be returned to the waiting list", incident_id
        )


def recover_unfinished_incidents(queue: DispatchQueue) -> int:
    """Queue again the calls that were accepted but never dispatched.

    Runs once at start-up. A call is REPORTED from the moment it is accepted
    until a worker has dealt with it. If the server stopped or crashed in
    between, the call is still REPORTED in the database, and nothing else
    would ever look at it again.

    Returns how many calls were recovered.
    """
    mav_settings = get_settings()
    try:
        with session_scope() as mav_session:
            mav_pending = mav_session.execute(
                select(Incident)
                .where(Incident.status == IncidentStatus.REPORTED)
                .order_by(Incident.reported_at, Incident.id)
            ).scalars().all()
            if not mav_pending:
                return 0
            mav_fleet = fleet_positions(mav_session)
            mav_requests = [
                make_request(mav_i, mav_fleet, mav_settings) for mav_i in mav_pending
            ]
    except Exception:
        # Starting without the database is still allowed; /health reports it.
        logger.exception("could not check for unfinished calls at start-up")
        return 0

    for mav_request in mav_requests:
        queue.put(mav_request)
    logger.warning(
        "recovered %d call(s) that were accepted before the last shutdown "
        "but not dispatched",
        len(mav_requests),
    )
    return len(mav_requests)


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


# A call is looked at only once it is this old: a new call is between its
# commit and its place in the queue for a few milliseconds.
LOST_AFTER_SECONDS = 10.0


def requeue_lost_incidents(queue, suspects: set[int]) -> tuple[int, set[int]]:
    """Queue again the calls lost from the shared queue in Redis.

    With several server processes a call can drop out of the queue while it is
    still REPORTED: a process stops just after its worker took the call, or
    Redis restarts and loses what it held. Nothing else would look at such a
    call until a server starts again.

    The clock calls this every few seconds. A call that is REPORTED but neither
    waiting in the queue nor dispatched is a suspect. It may only be in a
    worker's hands at that moment, so it is queued again only if it is still a
    suspect at the next look, by when a worker would long have finished with
    it. Even then, a second worker that takes it finds it handled already, or
    is refused a second dispatch by the unique key on dispatches.incident_id.

    Returns how many calls were queued again, and the suspects for next time.
    """
    mav_settings = get_settings()
    mav_cutoff = datetime.now() - timedelta(seconds=LOST_AFTER_SECONDS)
    with session_scope() as mav_session:
        mav_reported = mav_session.execute(
            select(Incident)
            .where(Incident.status == IncidentStatus.REPORTED, Incident.reported_at < mav_cutoff)
            .order_by(Incident.reported_at, Incident.id)
        ).scalars().all()
        if not mav_reported:
            return 0, set()
        try:
            mav_waiting = queue.waiting([mav_i.id for mav_i in mav_reported])
        except shared.REDIS_ERRORS:
            return 0, suspects  # cannot tell now; look again next time
        mav_now_suspect = {mav_i.id for mav_i in mav_reported if mav_i.id not in mav_waiting}
        mav_lost = [mav_i for mav_i in mav_reported if mav_i.id in mav_now_suspect & suspects]
        if not mav_lost:
            return 0, mav_now_suspect
        mav_fleet = fleet_positions(mav_session)
        mav_requests = [make_request(mav_i, mav_fleet, mav_settings) for mav_i in mav_lost]

    for mav_request in mav_requests:
        queue.put(mav_request)
        logger.warning(
            "incident=%s was accepted but had dropped out of the queue; queued again",
            mav_request.incident_id,
        )
    return len(mav_requests), mav_now_suspect - {mav_r.incident_id for mav_r in mav_requests}
