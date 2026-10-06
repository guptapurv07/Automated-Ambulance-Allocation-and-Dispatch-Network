"""Live view of the concurrency machinery.

Exists for observability: queue depth, worker activity and clock progress are
the numbers that show the system is genuinely running work in parallel rather
than one call at a time.
"""

from fastapi import APIRouter

from app.core.runtime import dispatch_queue, simulation_clock, worker_pool
from app.schemas import QueueStatsOut

router = APIRouter(prefix="/queue", tags=["concurrency"])


@router.get("", response_model=QueueStatsOut)
def queue_stats() -> QueueStatsOut:
    """Current queue depth, worker pool counters and simulation clock state."""
    return QueueStatsOut(
        queue=dispatch_queue.stats(),
        workers=worker_pool.stats(),
        clock=simulation_clock.stats(),
    )
