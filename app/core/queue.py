"""A thread-safe queue built directly from a condition variable.

The standard library has `queue.Queue`, and in ordinary application code that
is what you would use. This project implements its own because the queue *is*
one of the deliverables: the producer/consumer handoff, the critical section
protecting the shared buffer, and the scheduling policy all need to be visible
and variable rather than hidden behind a stdlib class.

The buffer is guarded by a single `threading.Condition`. Producers append and
signal; consumers wait on the condition until work arrives or the queue closes.
Because every access to `_pending` happens while holding the condition's lock,
the buffer itself is never touched by two threads at once.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


class QueueClosed(Exception):
    """Raised when work is submitted to a queue that has been shut down."""


@dataclass
class DispatchRequest:
    """One unit of work: an incident waiting to be allocated a vehicle."""

    incident_id: int
    severity: str
    enqueued_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def waited_ms(self) -> float:
        """How long this request has sat in the queue."""
        mav_delta = datetime.now(timezone.utc) - self.enqueued_at
        return mav_delta.total_seconds() * 1000


class Scheduler(Protocol):
    """Decides which pending request a free worker should take next.

    This is the seam for comparing scheduling disciplines. FIFO is implemented
    below; severity-priority and Least Laxity First slot in here without any
    change to the queue or the worker pool.
    """

    name: str

    def select(self, pending: list[DispatchRequest]) -> int:
        """Return the index of the request to dequeue. `pending` is non-empty."""
        ...


class FifoScheduler:
    """First in, first out. Fair, but ignores how urgent a call is."""

    name = "fifo"

    def select(self, pending: list[DispatchRequest]) -> int:
        return 0


class DispatchQueue:
    """A bounded-wait, thread-safe queue of pending dispatch requests."""

    def __init__(self, scheduler: Scheduler | None = None) -> None:
        self._pending: list[DispatchRequest] = []
        self._scheduler: Scheduler = scheduler or FifoScheduler()
        # One condition variable guards the buffer and signals waiting workers.
        self._condition = threading.Condition()
        self._closed = False
        self._enqueued_total = 0
        self._dequeued_total = 0
        self._peak_depth = 0

    @property
    def scheduler_name(self) -> str:
        return self._scheduler.name

    def put(self, request: DispatchRequest) -> None:
        """Append a request and wake one waiting worker."""
        with self._condition:
            if self._closed:
                raise QueueClosed("Queue is shut down; not accepting work")
            self._pending.append(request)
            self._enqueued_total += 1
            self._peak_depth = max(self._peak_depth, len(self._pending))
            self._condition.notify()

    def get(self, timeout: float | None = None) -> DispatchRequest | None:
        """Block until a request is available, then return it.

        Returns None if the wait timed out, or if the queue was closed and
        drained -- which is how workers learn it is time to exit.
        """
        with self._condition:
            while not self._pending and not self._closed:
                if not self._condition.wait(timeout):
                    return None  # timed out; caller loops and re-checks
            if not self._pending:
                return None  # closed and empty
            mav_index = self._scheduler.select(self._pending)
            request = self._pending.pop(mav_index)
            self._dequeued_total += 1
            return request

    def close(self) -> None:
        """Stop accepting work and wake every waiting worker so they can exit."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def depth(self) -> int:
        """How many requests are waiting right now."""
        with self._condition:
            return len(self._pending)

    def stats(self) -> dict[str, int | str]:
        with self._condition:
            return {
                "scheduler": self._scheduler.name,
                "depth": len(self._pending),
                "peak_depth": self._peak_depth,
                "enqueued_total": self._enqueued_total,
                "dequeued_total": self._dequeued_total,
            }
