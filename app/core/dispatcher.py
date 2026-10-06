"""The unit of work a worker thread performs.

This is deliberately thin. It opens a session, loads the incident, and hands
off to the allocator -- all of the ranking and locking logic lives in
app/core/allocation.py and is unchanged by the move to a thread pool.

That is the point of the design: the allocator was written to be called by one
thread or by many without modification, because its correctness comes from the
database row lock rather than from any assumption about how many callers exist.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.core.allocation import NoAmbulanceAvailable, allocate_ambulance
from app.core.queue import DispatchRequest
from app.db import session_scope
from app.models import Incident

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

        logger.info(
            "incident=%s picked up  severity=%s queue_wait=%.1fms",
            mav_incident.id,
            request.severity,
            mav_queue_wait,
        )

        try:
            allocate_ambulance(mav_session, mav_incident, get_settings())
        except NoAmbulanceAvailable as exc:
            # Already recorded as UNASSIGNED by the allocator; nothing to retry
            # here. A later milestone can re-queue these as vehicles free up.
            logger.warning("incident=%s not allocated: %s", mav_incident.id, exc)
