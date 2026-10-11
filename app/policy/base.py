"""Dispatch policy: decides the order in which candidate ambulances are tried.

The allocator ranks the free ambulances by distance (phase 1, no locks held), asks the
policy to put them in order, locks the first CANDIDATE_POOL_SIZE of that order, and claims
the first one that is still free. The policy proposes; the lock disposes.

A policy must be cheap and must not touch the database: it runs before the lock is taken.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.models import Incident


class Policy(Protocol):
    def order(
        self, incident: Incident, candidates: list[tuple[int, float]]
    ) -> list[tuple[int, float]]:
        """Return the same (ambulance_id, distance_km) candidates, best first.

        `candidates` arrives nearest first.
        """


class GreedyPolicy:
    """Nearest first, which is the order the candidates already arrive in."""

    def order(
        self, incident: Incident, candidates: list[tuple[int, float]]
    ) -> list[tuple[int, float]]:
        return list(candidates)
