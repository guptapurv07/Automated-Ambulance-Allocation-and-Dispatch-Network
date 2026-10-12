"""One-step lookahead: send the ambulance whose departure hurts the next call the least.

Among the nearest candidates each one is scored

    severity weight * its own travel time  +  beta * expected response time to the NEXT call
                                                     if this ambulance is the one that leaves

The expected response time to the next call is averaged over where incidents tend to happen (the
demand map), using the free ambulances that would still be there. beta = 0 is exactly "nearest".

Found by simulation (see docs/RL_PLAN.md): beta of about 1 to 1.5 beat "nearest" on unseen incident
streams under normal and heavy load and did no harm elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.policy.demand import CENTRES, DemandMap, cell_km

if TYPE_CHECKING:
    from app.models import Incident

# How much a minute of delay costs, by severity. Mean over the severity mix is about 1.4,
# which is also the beta the simulation settled on.
SEVERITY_WEIGHT = {"CRITICAL": 4.0, "HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.5}


class LookaheadPolicy:
    def __init__(
        self,
        demand: DemandMap,
        beta: float = 1.25,
        speed_kmph: float = 32.0,
        pool_size: int = 5,  # mirrors allocation.CANDIDATE_POOL_SIZE
    ) -> None:
        self.demand, self.beta, self.speed_kmph, self.pool_size = demand, beta, speed_kmph, pool_size

    def refresh(self, session) -> None:
        self.demand.refresh(session)

    def order(
        self,
        incident: Incident,
        candidates: list[tuple[int, float]],
        positions: dict[int, tuple[float, float]] | None = None,
    ) -> list[tuple[int, float]]:
        if len(candidates) < 2 or not positions:
            return list(candidates)

        mav_shares = self.demand.shares
        mav_to_min = 60.0 / self.speed_kmph
        mav_n = len(CENTRES)
        # For every demand cell: the closest free ambulance, who it is, and the runner-up.
        mav_best, mav_owner, mav_second = [float("inf")] * mav_n, [None] * mav_n, [float("inf")] * mav_n
        for mav_id, _ in candidates:
            mav_lat, mav_lon = positions[mav_id]
            for mav_c, mav_v in enumerate(cell_km(mav_lat, mav_lon)):
                if mav_v < mav_best[mav_c]:
                    mav_second[mav_c], mav_best[mav_c], mav_owner[mav_c] = mav_best[mav_c], mav_v, mav_id
                elif mav_v < mav_second[mav_c]:
                    mav_second[mav_c] = mav_v

        mav_severity = getattr(incident.severity, "value", incident.severity)
        mav_weight = SEVERITY_WEIGHT.get(str(mav_severity), 1.0)

        mav_pool = candidates[: self.pool_size]
        mav_scored = []
        for mav_id, mav_km in mav_pool:
            mav_next = sum(
                mav_shares[mav_c] * (mav_second[mav_c] if mav_owner[mav_c] == mav_id else mav_best[mav_c])
                for mav_c in range(mav_n)
            )
            mav_scored.append(
                (mav_weight * mav_km * mav_to_min + self.beta * mav_next * mav_to_min, mav_id, mav_km)
            )
        mav_scored.sort(key=lambda row: row[0])  # stable: ties keep nearest-first
        return [(mav_id, mav_km) for _, mav_id, mav_km in mav_scored] + list(candidates[self.pool_size :])
