"""Automated Ambulance Allocation and Dispatch Network -- API entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import ambulances, dispatches, health, incidents, queue
from app.config import get_settings
from app.core.dispatcher import recover_unfinished_incidents
from app.core.runtime import dispatch_queue, simulation_clock, worker_pool
from app.policy.factory import get_policy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(threadName)-12s  %(name)-20s  %(message)s",
    datefmt="%H:%M:%S",
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the background threads on boot, shut them down cleanly on exit.

    Order matters. The pool starts first so that no request can be queued
    before there is a worker to consume it. Calls that were accepted before
    the last shutdown but never dispatched are then put back on the queue. On
    exit the clock stops first and the pool last, and the pool finishes the
    queued calls before its workers stop.
    """
    # Fail now, not on the first call, if DISPATCH_POLICY is unknown or its weights file is missing.
    get_policy(settings)
    worker_pool.start()
    recover_unfinished_incidents(dispatch_queue)
    if settings.sim_clock_enabled:
        simulation_clock.start()
    try:
        yield
    finally:
        simulation_clock.stop()
        worker_pool.stop()


app = FastAPI(
    lifespan=lifespan,
    title="Automated Ambulance Allocation and Dispatch Network",
    description=(
        "Concurrency-safe emergency dispatch. Intake queues each call and a "
        "pool of worker threads allocates vehicles in parallel, claiming them "
        "inside short transactions using MySQL InnoDB row-level locking."
    ),
    version="0.2.0",
)

app.include_router(health.router)
app.include_router(ambulances.router)
app.include_router(incidents.router)
app.include_router(dispatches.router)
app.include_router(queue.router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str | int]:
    return {
        "service": "Automated Ambulance Allocation and Dispatch Network",
        "docs": "/docs",
        "locking_mode": settings.locking_mode,
        "worker_pool_size": settings.worker_pool_size,
    }
