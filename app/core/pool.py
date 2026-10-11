"""A pool of worker threads that consume the dispatch queue.

Python's `concurrent.futures.ThreadPoolExecutor` would do this in three lines.
This project builds its own because the pool is one of the deliverables: thread
lifecycle, the consumer side of the producer/consumer handoff, and graceful
shutdown all need to be visible rather than hidden inside a stdlib class.

Each worker is a long-lived thread running the same loop: block on the queue,
take a request, process it, repeat. Workers are named `worker-1`, `worker-2`
and so on, which is what makes concurrency visible in the logs -- interleaved
thread names in the output are the proof that allocation is genuinely parallel.

A worker never shares a database session with another worker. Each call into
the handler opens its own session, because a SQLAlchemy Session is not
thread-safe and sharing one would corrupt results in ways that are extremely
hard to diagnose.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from app.core.queue import DispatchQueue, DispatchRequest

logger = logging.getLogger("dispatch.pool")

# How long a worker blocks on an empty queue before looping to re-check
# whether it has been asked to shut down.
POLL_TIMEOUT_SECONDS = 0.5


class WorkerPool:
    """A fixed set of threads draining a shared queue."""

    def __init__(
        self,
        queue: DispatchQueue,
        handler: Callable[[DispatchRequest], None],
        size: int = 4,
        name_prefix: str = "worker",
    ) -> None:
        if size < 1:
            raise ValueError("Worker pool size must be at least 1")
        self._queue = queue
        self._handler = handler
        self._size = size
        self._name_prefix = name_prefix
        self._threads: list[threading.Thread] = []
        self._stopping = threading.Event()
        # Counters are incremented by several threads, so they get their own
        # lock rather than relying on any assumption about atomicity.
        self._counter_lock = threading.Lock()
        self._handled = 0
        self._failed = 0

    @property
    def size(self) -> int:
        return self._size

    def start(self) -> None:
        if self._threads:
            return
        self._stopping.clear()
        for mav_index in range(1, self._size + 1):
            mav_thread = threading.Thread(
                target=self._run,
                name=f"{self._name_prefix}-{mav_index}",
                daemon=True,
            )
            mav_thread.start()
            self._threads.append(mav_thread)
        logger.info("worker pool started  size=%d", self._size)

    def stop(self, timeout: float = 10.0) -> None:
        """Finish the queued work, then stop the workers.

        Closing the queue stops new work coming in and wakes every idle
        worker. The workers keep taking requests until the queue is empty and
        only then exit, so a call that was already accepted is still
        dispatched. `timeout` is the total time allowed for that. If it runs
        out, each worker finishes the call it is on and stops; the calls still
        queued stay recorded in the database and are picked up again the next
        time the server starts (see recover_unfinished_incidents).
        """
        if not self._threads:
            return
        self._queue.close()
        mav_deadline = time.monotonic() + timeout
        for mav_thread in self._threads:
            mav_thread.join(timeout=max(0.0, mav_deadline - time.monotonic()))

        if any(t.is_alive() for t in self._threads):
            self._stopping.set()  # out of time: finish the current call only
            for mav_thread in self._threads:
                mav_thread.join(timeout=POLL_TIMEOUT_SECONDS * 4)
            logger.warning(
                "stopped with %d call(s) still queued; they will be dispatched "
                "at the next start",
                self._queue.depth,
            )
        mav_alive = [t.name for t in self._threads if t.is_alive()]
        if mav_alive:
            logger.warning("workers did not exit cleanly: %s", ", ".join(mav_alive))
        self._threads = []
        logger.info(
            "worker pool stopped  handled=%d failed=%d", self._handled, self._failed
        )

    def _run(self) -> None:
        """The worker loop. One of these runs per thread."""
        while not self._stopping.is_set():
            mav_request = self._queue.get(timeout=POLL_TIMEOUT_SECONDS)

            if mav_request is None:
                # Either the wait timed out, or the queue closed and drained.
                if self._queue.is_closed:
                    break
                continue

            try:
                self._handler(mav_request)
                with self._counter_lock:
                    self._handled += 1
            except Exception:
                # One bad request must never kill a worker thread.
                with self._counter_lock:
                    self._failed += 1
                logger.exception(
                    "worker failed on incident=%s", mav_request.incident_id
                )

        logger.debug("%s exiting", threading.current_thread().name)

    def stats(self) -> dict[str, int | bool]:
        with self._counter_lock:
            return {
                "size": self._size,
                "running": any(t.is_alive() for t in self._threads),
                "handled": self._handled,
                "failed": self._failed,
            }
