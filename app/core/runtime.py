"""Process-wide concurrency components.

The queue, the worker pool and the simulation clock are created once per
process and shared: the API layer is the producer, the pool is the consumer,
and the clock cycles vehicles back into the available fleet and re-queues the
incidents that were still waiting for one.

They live here rather than in app/main.py so that route modules can reach them
without importing the FastAPI application, which would be circular.

With REDIS_URL set, the queue is the one in Redis that every server process
shares (app/core/redis_queue.py), and the clock runs in one process at a time.
"""

from __future__ import annotations

from app.config import get_settings
from app.core.dispatcher import handle_dispatch_request
from app.core.pool import WorkerPool
from app.core.queue import DispatchQueue, scheduler_by_name
from app.core.redis_queue import RedisDispatchQueue
from sim.clock import SimulationClock

settings = get_settings()

_scheduler = scheduler_by_name(settings.queue_scheduler)
# With REDIS_URL set, every server process takes calls from one queue in Redis.
# Without it, the queue lives in this process's memory.
dispatch_queue: DispatchQueue | RedisDispatchQueue = (
    RedisDispatchQueue(_scheduler) if settings.redis_url else DispatchQueue(scheduler=_scheduler)
)

worker_pool = WorkerPool(
    queue=dispatch_queue,
    handler=handle_dispatch_request,
    size=settings.worker_pool_size,
)

simulation_clock = SimulationClock(settings, queue=dispatch_queue)
