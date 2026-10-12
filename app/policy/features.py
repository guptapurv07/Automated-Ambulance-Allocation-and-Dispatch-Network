"""State encoding for the table-based (Q-learning) experiments in rl/.

Five inputs, all relative so one table works for any fleet size: severity, fraction of fleet free,
share of incidents that lose cover if rank 0 leaves, gap between rank 0 and rank 1, time until the
soonest busy ambulance is free. The lookahead policy in app/policy/lookahead.py does not use this file.
"""

from app.config import get_settings
from app.geo import haversine_km
from app.policy.demand import BBOX, CENTRES, GRID, DemandMap, cell_of  # noqa: F401  (re-exported for rl/)

SPEED_KMPH = get_settings().avg_speed_kmph   # AVG_SPEED_KMPH in config.py, so there is one place to change it
COVERAGE_MINUTES = 8.0               # a cell is "covered" if a free ambulance is this close
COVERAGE_KM = SPEED_KMPH * COVERAGE_MINUTES / 60.0
N_ACTIONS = 5                        # rank 0..4, same as allocation.CANDIDATE_POOL_SIZE
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
N_STATES = 4 * 3 * 3 * 3 * 3
POINTS = CENTRES

# The demand map the simulator trains and tests with.
DEMAND_MAP = DemandMap()


def set_demand(coords):
    DEMAND_MAP.set_from(coords)


def point_mask(lat, lon):
    """Bitmask of the cells a free ambulance at (lat, lon) can reach in time."""
    mask = 0
    for bit, (plat, plon) in enumerate(POINTS):
        if haversine_km(lat, lon, plat, plon) <= COVERAGE_KM:
            mask |= 1 << bit
    return mask


def demand_share(mask):
    """Share of incidents that fall in the cells selected by `mask`."""
    shares = DEMAND_MAP.shares
    return sum(shares[b] for b in range(len(POINTS)) if mask >> b & 1)


def encode_state(severity, free_frac, lost_share, gap_minutes, next_free_minutes):
    """Bucket the five inputs into one state index in [0, N_STATES)."""
    f = 0 if free_frac < 0.34 else 1 if free_frac <= 0.67 else 2
    lost = 0 if lost_share < 1e-9 else 1 if lost_share <= 0.05 else 2
    gap = 0 if gap_minutes < 1.5 else 1 if gap_minutes <= 4.0 else 2
    nxt = 0 if next_free_minutes < 3.0 else 1 if next_free_minutes <= 8.0 else 2
    return (((SEVERITIES.index(severity) * 3 + f) * 3 + lost) * 3 + gap) * 3 + nxt
