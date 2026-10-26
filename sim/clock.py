"""Background clock that advances dispatches through their lifecycle.

Without this, a vehicle claimed by the allocator stays claimed forever: the
fleet drains to nothing after one call per ambulance and no further allocation can
succeed. The clock is what makes the system a *simulation* rather than a
one-shot form, and it is what makes sustained concurrency testing possible --
there is no contention to measure if there is nothing left to contend over.

It runs on its own thread and holds its own database session, following the
same rule the worker pool will: a session is never shared across threads.

Time is compressed by `sim_time_scale`. At the default of 60, one simulated
minute elapses per real second, so a call that would occupy a vehicle for
roughly 25 minutes releases it after about 25 seconds of demo time.

The clock also drives the retry of incidents that found no vehicle. After
advancing dispatches, each tick hands waiting incidents back to the dispatch
queue while vehicles are free. With the clock disabled nothing is retried.

With several server processes and REDIS_URL set, one clock runs for all of
them: the one whose process holds the clock's lease in Redis. The lease is
renewed every tick; if that process stops, the lease runs out after a few
ticks and the clock of another process takes over.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload

from app.config import Settings
from app.core.dispatcher import requeue_lost_incidents, requeue_waiting_incidents
from app.core.lifecycle import (
    InvalidTransition,
    advance_dispatch,
    phase_duration_minutes,
)
from app.core.queue import DispatchQueue
from app.core.shared import Lease
from app.db import session_scope
from app.models import Ambulance, AmbulanceStatus, Dispatch, DispatchStatus

logger = logging.getLogger("dispatch.clock")

# How often the clock looks for calls lost from the shared queue in Redis. A
# call is queued again only if two looks in a row find it lost, so a worker
# that is still busy with it has this long to finish.
LOOK_FOR_LOST_SECONDS = 10.0


class SimulationClock:
    """Advances due dispatches once per tick, on a dedicated thread."""

    def __init__(
        self, settings: Settings, queue: DispatchQueue | None = None
    ) -> None:
        self._settings = settings
        # Where waiting incidents are handed back for another attempt. Without
        # a queue the clock only advances dispatches.
        self._queue = queue
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        # One clock across server processes (with Redis): the lease outlives
        # a few missed ticks, so a slow tick does not hand the clock over.
        self._lease = Lease("clock", seconds=max(5.0, 5 * settings.sim_tick_seconds))
        # Calls that seemed to have dropped out of the shared queue at the
        # last look, and when the next look is due (see requeue_lost_incidents).
        self._suspects: set[int] = set()
        self._next_look = 0.0
        self._ticks = 0
        self._transitions = 0
        self._requeued = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="sim-clock", daemon=True
        )
        self._thread.start()
        logger.info(
            "simulation clock started  time_scale=%.0fx  tick=%.1fs",
            self._settings.sim_time_scale,
            self._settings.sim_tick_seconds,
        )

    def stop(self, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        self._stop_event.set()  # wakes the wait() immediately, no shutdown lag
        self._thread.join(timeout=timeout)
        self._lease.give_up()  # another process's clock can take over at once
        logger.info(
            "simulation clock stopped  ticks=%d transitions=%d requeued=%d",
            self._ticks,
            self._transitions,
            self._requeued,
        )
        self._thread = None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                # Without Redis this process always holds the lease.
                if self._lease.hold():
                    self._tick()
                    self._retry_waiting()
            except Exception:  # a bad tick must not kill the clock thread
                logger.exception("simulation tick failed")
            self._ticks += 1
            self._stop_event.wait(self._settings.sim_tick_seconds)

    def _tick(self) -> None:
        """Advance every dispatch whose current phase has elapsed."""
        mav_scale = self._settings.sim_time_scale
        mav_now = datetime.now()

        with session_scope() as mav_session:
            # One query for the whole tick: every active dispatch with its
            # vehicle's status, its incident and its hospital. Fetching those
            # one dispatch at a time costs a round trip per vehicle on a call,
            # which makes the clock run slow as the fleet grows.
            mav_active = mav_session.execute(
                select(Dispatch, Ambulance.status)
                .join(Ambulance, Ambulance.id == Dispatch.ambulance_id)
                .where(Dispatch.status == DispatchStatus.ACTIVE)
                .options(joinedload(Dispatch.incident), joinedload(Dispatch.hospital))
            ).all()

            # Each due dispatch with the status it was seen in.
            mav_due: list[tuple[int, AmbulanceStatus]] = []
            for mav_dispatch, mav_status in mav_active:
                mav_required_minutes = phase_duration_minutes(
                    mav_status,
                    mav_dispatch,
                    self._settings.avg_speed_kmph,
                )
                # Simulated minutes -> real seconds.
                mav_required_seconds = (mav_required_minutes * 60.0) / mav_scale
                mav_elapsed_seconds = (mav_now - mav_dispatch.phase_started_at).total_seconds()
                if mav_elapsed_seconds >= mav_required_seconds:
                    mav_due.append((mav_dispatch.id, mav_status))

            for mav_dispatch_id, mav_seen in mav_due:
                try:
                    # `expected` makes the step conditional on what was seen
                    # above. If something else advanced this dispatch in the
                    # meantime, it is left alone until it is due again.
                    advance_dispatch(mav_session, mav_dispatch_id, expected=mav_seen)
                    self._transitions += 1
                except InvalidTransition as exc:
                    logger.warning("dispatch=%s skipped: %s", mav_dispatch_id, exc)
                except OperationalError as exc:
                    # The vehicle's row stayed locked for too long. Leave this
                    # dispatch for the next tick; the others must not wait.
                    mav_session.rollback()
                    logger.warning(
                        "dispatch=%s not advanced this tick: %s",
                        mav_dispatch_id,
                        getattr(exc.orig, "args", [exc])[-1],
                    )

    def _retry_waiting(self) -> None:
        """Hand incidents that found no vehicle back to the queue."""
        if self._queue is not None:
            self._requeued += requeue_waiting_incidents(self._queue)
            # The shared queue in Redis can lose a call (see
            # requeue_lost_incidents); look for one every 10 real seconds.
            if hasattr(self._queue, "waiting") and time.monotonic() >= self._next_look:
                self._next_look = time.monotonic() + LOOK_FOR_LOST_SECONDS
                mav_found, self._suspects = requeue_lost_incidents(self._queue, self._suspects)
                self._requeued += mav_found

    def stats(self) -> dict[str, float | int | bool]:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            # Is this process's clock the one moving the ambulances? Always,
            # without Redis.
            "leader": self._lease.held if self._thread is not None else False,
            "ticks": self._ticks,
            "transitions": self._transitions,
            "requeued": self._requeued,
            "time_scale": self._settings.sim_time_scale,
        }
