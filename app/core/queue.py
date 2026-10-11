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

Which request a free worker takes next is decided by a scheduler. Three are
provided so they can be compared on the same load:

    fifo      arrival order
    priority  most severe first
    llf       Least Laxity First: the call with the least spare time before
              its response deadline goes first
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
    # When an ambulance should have reached this patient at the latest, and
    # how long the trip there is expected to take. Both are set by
    # app/core/scheduling.py; together they give the request's laxity.
    deadline_at: datetime
    travel_seconds: float = 0.0
    enqueued_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def waited_ms(self) -> float:
        """How long this request has sat in the queue."""
        mav_delta = datetime.now(timezone.utc) - self.enqueued_at
        return mav_delta.total_seconds() * 1000

    def laxity_seconds(self, now: datetime | None = None) -> float:
        """Spare time left before this call can no longer be reached on time.

        laxity = time until the deadline - time the trip still needs

        It shrinks for as long as the call waits, and goes negative once the
        deadline cannot be met any more.
        """
        mav_now = now or datetime.now(timezone.utc)
        return (self.deadline_at - mav_now).total_seconds() - self.travel_seconds

class Scheduler(Protocol):
    """Decides which pending request a free worker should take next.

    This is the seam for comparing scheduling disciplines. The three below
    plug in here without any change to the queue or the worker pool.
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

# Lower rank is served first.
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

class PriorityScheduler:
    """Most severe call first; calls of equal severity in arrival order.

    A static priority: a call's place never improves while it waits. Under
    steady load a LOW call can be passed again and again by newer, more severe
    ones, which is starvation.
    """

    name = "priority"

    def select(self, pending: list[DispatchRequest]) -> int:
        return min(
            range(len(pending)),
            key=lambda mav_i: (
                SEVERITY_RANK.get(pending[mav_i].severity, len(SEVERITY_RANK)),
                mav_i,
            ),
        )

class LeastLaxityScheduler:
    """Least Laxity First: the call with the least spare time goes first.

    A dynamic priority. Laxity falls for every second a call waits, so even a
    LOW call eventually becomes the most urgent one in the queue. That is what
    bounds its wait and prevents the starvation static priority allows.
    """

    name = "llf"

    def select(self, pending: list[DispatchRequest]) -> int:
        mav_now = datetime.now(timezone.utc)
        return min(
            range(len(pending)),
            key=lambda mav_i: (pending[mav_i].laxity_seconds(mav_now), mav_i),
        )

SCHEDULERS: dict[str, type] = {
    FifoScheduler.name: FifoScheduler,
    PriorityScheduler.name: PriorityScheduler,
    LeastLaxityScheduler.name: LeastLaxityScheduler,
}

def scheduler_by_name(name: str) -> Scheduler:
    """Build the scheduler selected by the QUEUE_SCHEDULER setting."""
    try:
        return SCHEDULERS[name.lower()]()
    except KeyError:
        raise ValueError(
            f"Unknown QUEUE_SCHEDULER {name!r}; choose one of {sorted(SCHEDULERS)}"
        ) from None

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
    def scheduler(self) -> Scheduler:
        """The policy in use. The retry path applies the same one."""
        return self._scheduler

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
