"""Learned dispatch policy: the weights are trained by reinforcement learning (policy gradient) in the
simulator. See rl/pg.py for the training and docs/RL_PLAN.md for the results.

Each of the nearest candidates gets a feature vector, and the learned weights turn it into a score; the
lowest score goes first. The weights live in learned_weights.json next to this file, written by
`python -m rl.pg train`. The simulator trains this same class, so training and serving cannot disagree.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from app.policy.demand import CENTRES, DemandMap, cell_km
from app.policy.lookahead import SEVERITY_WEIGHT

if TYPE_CHECKING:
    from app.models import Incident

WEIGHTS_FILE = Path(__file__).with_name("learned_weights.json")
FEATURES = (
    "own_eta_x_severity",  # minutes this ambulance needs, times the severity weight
    "next_call_cost",      # extra expected response time to the next call if this one leaves
    "extra_eta",           # minutes slower than the nearest candidate
    "coverage_lost",       # share of demand no other free ambulance can reach within 8 minutes
    "next_x_severity",     # next_call_cost times the severity weight
    "rank",                # 0 = nearest
)
SCALE = (10.0, 2.0, 5.0, 0.2, 8.0, 4.0)  # keeps every feature near 0..1, so the weights are comparable
COVERAGE_MINUTES = 8.0


class LearnedPolicy:
    def __init__(
        self,
        demand: DemandMap,
        weights: list[float] | None = None,
        speed_kmph: float = 32.0,
        pool_size: int = 5,  # mirrors allocation.CANDIDATE_POOL_SIZE
    ) -> None:
        if weights is None:
            mav_saved = json.loads(WEIGHTS_FILE.read_text())
            if tuple(mav_saved["features"]) != FEATURES:
                raise ValueError(f"{WEIGHTS_FILE.name} was trained on other features; retrain with rl.pg")
            weights = mav_saved["weights"]
        self.demand, self.weights, self.speed_kmph, self.pool_size = demand, list(weights), speed_kmph, pool_size

    def refresh(self, session) -> None:
        self.demand.refresh(session)

    def features(
        self,
        incident: Incident,
        candidates: list[tuple[int, float]],
        positions: dict[int, tuple[float, float]],
    ) -> list[list[float]]:
        """Scaled feature vector for each of the first `pool_size` candidates, in candidate order."""
        mav_shares = self.demand.shares
        mav_to_min = 60.0 / self.speed_kmph
        mav_reach_km = self.speed_kmph * COVERAGE_MINUTES / 60.0
        mav_n = len(CENTRES)
        # For every demand cell: the closest free ambulance, who it is, the runner-up, and how many cover it.
        mav_best, mav_owner, mav_second = [float("inf")] * mav_n, [None] * mav_n, [float("inf")] * mav_n
        mav_covers = [0] * mav_n
        for mav_id, _ in candidates:
            for mav_c, mav_v in enumerate(cell_km(*positions[mav_id])):
                if mav_v < mav_best[mav_c]:
                    mav_second[mav_c], mav_best[mav_c], mav_owner[mav_c] = mav_best[mav_c], mav_v, mav_id
                elif mav_v < mav_second[mav_c]:
                    mav_second[mav_c] = mav_v
                if mav_v <= mav_reach_km:
                    mav_covers[mav_c] += 1
        mav_base = sum(mav_shares[mav_c] * mav_best[mav_c] for mav_c in range(mav_n))

        mav_severity = getattr(incident.severity, "value", incident.severity)
        mav_weight = SEVERITY_WEIGHT.get(str(mav_severity), 1.0)
        mav_eta0 = candidates[0][1] * mav_to_min

        mav_rows = []
        for mav_rank, (mav_id, mav_km) in enumerate(candidates[: self.pool_size]):
            mav_cells = cell_km(*positions[mav_id])
            mav_next = (
                sum(
                    mav_shares[mav_c] * (mav_second[mav_c] if mav_owner[mav_c] == mav_id else mav_best[mav_c])
                    for mav_c in range(mav_n)
                )
                - mav_base
            ) * mav_to_min
            mav_lost = sum(
                mav_shares[mav_c]
                for mav_c in range(mav_n)
                if mav_covers[mav_c] == 1 and mav_cells[mav_c] <= mav_reach_km
            )
            mav_eta = mav_km * mav_to_min
            mav_raw = (mav_weight * mav_eta, mav_next, mav_eta - mav_eta0, mav_lost, mav_next * mav_weight, mav_rank)
            mav_rows.append([mav_v / mav_s for mav_v, mav_s in zip(mav_raw, SCALE)])
        return mav_rows

    def scores(self, rows: list[list[float]]) -> list[float]:
        return [sum(mav_w * mav_x for mav_w, mav_x in zip(self.weights, mav_row)) for mav_row in rows]

    def order(
        self,
        incident: Incident,
        candidates: list[tuple[int, float]],
        positions: dict[int, tuple[float, float]] | None = None,
    ) -> list[tuple[int, float]]:
        if len(candidates) < 2 or not positions:
            return list(candidates)
        mav_scores = self.scores(self.features(incident, candidates, positions))
        mav_pool = candidates[: self.pool_size]
        mav_best_first = sorted(range(len(mav_pool)), key=mav_scores.__getitem__)  # stable: ties keep nearest-first
        return [mav_pool[mav_i] for mav_i in mav_best_first] + list(candidates[self.pool_size :])
