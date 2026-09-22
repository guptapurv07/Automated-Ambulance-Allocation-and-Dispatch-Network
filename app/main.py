"""Automated Ambulance Allocation and Dispatch Network -- API entry point."""

import logging

from fastapi import FastAPI

from app.api import ambulances, dispatches, health, incidents
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(threadName)-12s  %(name)-20s  %(message)s",
    datefmt="%H:%M:%S",
)

settings = get_settings()

app = FastAPI(
    title="Automated Ambulance Allocation and Dispatch Network",
    description=(
        "Concurrency-safe emergency dispatch. Allocation runs in two phases: "
        "candidates are ranked without locks, then claimed inside a short "
        "transaction using MySQL InnoDB row-level locking."
    ),
    version="0.1.0",
)

app.include_router(health.router)
app.include_router(ambulances.router)
app.include_router(incidents.router)
app.include_router(dispatches.router)


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {
        "service": "Automated Ambulance Allocation and Dispatch Network",
        "docs": "/docs",
        "locking_mode": settings.locking_mode,
    }
