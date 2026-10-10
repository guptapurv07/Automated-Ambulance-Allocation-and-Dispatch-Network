"""Background clock that advances dispatches through their lifecycle.

Without this, a vehicle claimed by the allocator stays claimed forever: the
fleet drains to nothing after twelve calls and no further allocation can
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
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy import select

from app.config import Settings
from app.core.dispatcher import requeue_waiting_incidents
from app.core.lifecycle import (
    InvalidTransition,
    advance_dispatch,
    phase_duration_minutes,
)
from app.core.queue import DispatchQueue
from app.db import session_scope
from app.models import Ambulance, Dispatch, DispatchStatus

logger = logging.getLogger("dispatch.clock")


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
            mav_active = mav_session.execute(
                select(Dispatch).where(Dispatch.status == DispatchStatus.ACTIVE)
            ).scalars().all()

            mav_due: list[int] = []
            for mav_dispatch in mav_active:
                mav_ambulance = mav_session.get(Ambulance, mav_dispatch.ambulance_id)
                if mav_ambulance is None:
                    continue
                mav_required_minutes = phase_duration_minutes(
                    mav_ambulance.status,
                    mav_dispatch,
                    self._settings.avg_speed_kmph,
                )
                # Simulated minutes -> real seconds.
                mav_required_seconds = (mav_required_minutes * 60.0) / mav_scale
                mav_elapsed_seconds = (mav_now - mav_dispatch.phase_started_at).total_seconds()
                if mav_elapsed_seconds >= mav_required_seconds:
                    mav_due.append(mav_dispatch.id)

            for mav_dispatch_id in mav_due:
                try:
                    advance_dispatch(mav_session, mav_dispatch_id)
                    self._transitions += 1
                except InvalidTransition as exc:
                    logger.warning("dispatch=%s skipped: %s", mav_dispatch_id, exc)

    def _retry_waiting(self) -> None:
        """Hand incidents that found no vehicle back to the queue."""
        if self._queue is not None:
            self._requeued += requeue_waiting_incidents(self._queue)

    def stats(self) -> dict[str, float | int | bool]:
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "ticks": self._ticks,
            "transitions": self._transitions,
            "requeued": self._requeued,
            "time_scale": self._settings.sim_time_scale,
        }
