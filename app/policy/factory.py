"""Chooses the dispatch policy from settings. One instance is shared by all worker threads."""

from __future__ import annotations

from app.config import Settings
from app.policy.base import GreedyPolicy, Policy
from app.policy.demand import DemandMap
from app.policy.lookahead import LookaheadPolicy

_cache: dict[tuple, Policy] = {}


def get_policy(settings: Settings) -> Policy:
    key = (settings.dispatch_policy, settings.lookahead_beta, settings.avg_speed_kmph)
    if key not in _cache:
        if settings.dispatch_policy == "greedy":
            _cache[key] = GreedyPolicy()
        elif settings.dispatch_policy == "lookahead":
            _cache[key] = LookaheadPolicy(
                DemandMap(), beta=settings.lookahead_beta, speed_kmph=settings.avg_speed_kmph
            )
        else:
            raise ValueError(f"Unknown DISPATCH_POLICY: {settings.dispatch_policy!r}")
    return _cache[key]
