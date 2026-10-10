"""Process-wide concurrency components.

The queue, the worker pool and the simulation clock are created once per
process and shared: the API layer is the producer, the pool is the consumer,
and the clock cycles vehicles back into the available fleet and re-queues the
incidents that were still waiting for one.

They live here rather than in app/main.py so that route modules can reach them
without importing the FastAPI application, which would be circular.
"""

from __future__ import annotations

from app.config import get_settings
from app.core.dispatcher import handle_dispatch_request
from app.core.pool import WorkerPool
from app.core.queue import DispatchQueue, FifoScheduler
from sim.clock import SimulationClock

settings = get_settings()

dispatch_queue = DispatchQueue(scheduler=FifoScheduler())

worker_pool = WorkerPool(
    queue=dispatch_queue,
    handler=handle_dispatch_request,
    size=settings.worker_pool_size,
)

simulation_clock = SimulationClock(settings, queue=dispatch_queue)
