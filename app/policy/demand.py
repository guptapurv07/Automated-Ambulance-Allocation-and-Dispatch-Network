"""Where incidents happen: the share of all incidents that fall in each cell of a 5x5 grid over the
service area. A policy uses it to judge how much it would hurt to take an ambulance away from a place.

Learnt from the incidents table, so it follows the real traffic. Until there is enough history it is
uniform, which makes the lookahead policy behave almost like "nearest" (it does no harm).
"""

from __future__ import annotations

import time

BBOX = (30.17, 30.39, 77.85, 78.13)  # lat_lo, lat_hi, lon_lo, lon_hi: the Dehradun service area
GRID = 5

# Centre of every cell, row by row from the south-west.
CENTRES: list[tuple[float, float]] = [
    (
        BBOX[0] + (i + 0.5) * (BBOX[1] - BBOX[0]) / GRID,
        BBOX[2] + (j + 0.5) * (BBOX[3] - BBOX[2]) / GRID,
    )
    for i in range(GRID)
    for j in range(GRID)
]


def cell_of(lat: float, lon: float) -> int:
    i = min(GRID - 1, max(0, int((lat - BBOX[0]) / (BBOX[1] - BBOX[0]) * GRID)))
    j = min(GRID - 1, max(0, int((lon - BBOX[2]) / (BBOX[3] - BBOX[2]) * GRID)))
    return i * GRID + j


class DemandMap:
    def __init__(self, max_age_s: float = 30.0, history: int = 2000, min_incidents: int = 50) -> None:
        self.shares: list[float] = [1.0 / len(CENTRES)] * len(CENTRES)
        self.max_age_s, self.history, self.min_incidents = max_age_s, history, min_incidents
        self._loaded_at = float("-inf")

    def set_from(self, coords) -> None:
        """Learn the shares from (lat, lon) pairs. The list is replaced, never edited in place, so
        a worker thread reading it is never handed a half-updated map."""
        counts = [0] * len(CENTRES)
        for lat, lon in coords:
            counts[cell_of(lat, lon)] += 1
        total = float(sum(counts)) or 1.0
        self.shares = [c / total for c in counts]

    def refresh(self, session) -> None:
        """Re-read recent incidents, at most once per `max_age_s`. Runs in phase 1, holding no lock."""
        now = time.monotonic()
        if now - self._loaded_at < self.max_age_s:
            return
        self._loaded_at = now
        from sqlalchemy import select  # imported here so the simulator can use this module without it

        from app.models import Incident

        rows = session.execute(
            select(Incident.lat, Incident.lon).order_by(Incident.id.desc()).limit(self.history)
        ).all()
        if len(rows) >= self.min_incidents:
            self.set_from(rows)
