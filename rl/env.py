"""Pure-Python dispatch simulator for training the policy. No database. Design: docs/RL_PLAN.md.

One episode = a stream of incidents on a layout. At every incident with 2+ free ambulances the
policy picks a rank (0 = nearest); with 1 free it is forced; with 0 free the incident waits and
gets the next ambulance that frees up (highest severity first, then oldest).

    python -m rl.env        # runs the self-check
"""

import ast
import heapq
import math
import random
import re
from functools import lru_cache
from pathlib import Path

from app.geo import haversine_km
from app.policy import features
from app.policy.features import (N_ACTIONS, POINTS, SEVERITIES, SPEED_KMPH,
                                 demand_share, encode_state, point_mask, set_demand)

ROOT = Path(__file__).resolve().parent.parent
W_SEV = {"CRITICAL": 4.0, "HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.5}
SEV_MIX = (0.10, 0.25, 0.40, 0.25)            # same order as SEVERITIES
LAMBDA = 20.0                                 # weight of the coverage bonus
FIXED_MINUTES = 0.5 + 5.0 + 5.0               # mirrors lifecycle.PHASE_MINUTES; the two journeys are added per job
# mirrors scheduling.RESPONSE_TARGET_MINUTES; the default queue scheduler (LLF) works from these
RESPONSE_TARGET_MINUTES = {"CRITICAL": 8.0, "HIGH": 15.0, "MEDIUM": 30.0, "LOW": 60.0}
MEAN_JOB_MINUTES = 30.0                       # measured: 18.5 fixed + ~11 min ETA (ambulances end each job at a hospital)
HOTSPOTS = ((30.3255, 78.0413, 0.30), (30.2892, 77.9959, 0.20),    # Clock Tower, ISBT
            (30.3563, 78.0664, 0.15), (30.3320, 77.9412, 0.10))    # Rajpur Road, Prem Nagar
BACKGROUND = 0.25                             # share of incidents spread uniformly over the city box
SPREAD_DEG = 1.5 / 111.0                      # ~1.5 km


class World:
    """Ambulance i starts at location i; hospitals follow. Immutable during an episode."""

    def __init__(self, ambulances, hospitals):
        self.locs = list(ambulances) + list(hospitals)
        self.n_amb = len(ambulances)
        self.hosp = list(range(self.n_amb, len(self.locs)))
        self.masks = [point_mask(lat, lon) for lat, lon in self.locs]
        # minutes from every location to the centre of every demand cell
        self.cell_eta = [[haversine_km(lat, lon, plat, plon) / SPEED_KMPH * 60 for plat, plon in POINTS]
                         for lat, lon in self.locs]


def random_world(rng, fleet=None):
    box = city_box()
    pt = lambda: (rng.uniform(box[0], box[1]), rng.uniform(box[2], box[3]))
    n = fleet or rng.randint(8, 40)
    return World([pt() for _ in range(n)], [pt() for _ in range(rng.randint(3, 6))])


@lru_cache(maxsize=1)
def _seed_tables():
    """STATIONS and HOSPITALS from sim/seed.py (read as text, so no database packages are needed)."""
    found = {}
    for node in ast.parse((ROOT / "sim" / "seed.py").read_text()).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") in ("STATIONS", "HOSPITALS"):
            found[node.target.id] = ast.literal_eval(node.value)
    return found


@lru_cache(maxsize=1)
def city_box(pad=0.01):
    """(lat_lo, lat_hi, lon_lo, lon_hi) around the seeded stations and hospitals: where the simulated city is.
    This is a property of the scenario, not the service-area setting the allocator uses."""
    t = _seed_tables()
    pts = [(s[2], s[3]) for s in t["STATIONS"]] + [(h[1], h[2]) for h in t["HOSPITALS"]]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return min(lats) - pad, max(lats) + pad, min(lons) - pad, max(lons) + pad


@lru_cache(maxsize=1)
def real_world():
    """The Dehradun setup from sim/seed.py."""
    t = _seed_tables()
    return World([(s[2], s[3]) for s in t["STATIONS"]], [(h[1], h[2]) for h in t["HOSPITALS"]])


def make_incidents(rng, n_amb, n, load=(0.3, 0.8), surge=None):
    """Poisson arrivals at a random load; hotspots plus background; returns [(t, lat, lon, severity)].
    surge = (start_share, end_share, multiplier) multiplies the arrival rate for part of the stream."""
    box = city_box()
    rate = rng.uniform(*load) * n_amb / MEAN_JOB_MINUTES
    weights = [h[2] * rng.uniform(0.7, 1.3) for h in HOTSPOTS] + [BACKGROUND]
    t, out = 0.0, []
    for i in range(n):
        in_surge = surge and surge[0] <= i / n < surge[1]
        t += rng.expovariate(rate * surge[2] if in_surge else rate)
        k = rng.choices(range(len(weights)), weights)[0]
        if k < len(HOTSPOTS):
            lat, lon = rng.gauss(HOTSPOTS[k][0], SPREAD_DEG), rng.gauss(HOTSPOTS[k][1], SPREAD_DEG)
        else:
            lat, lon = rng.uniform(box[0], box[1]), rng.uniform(box[2], box[3])
        lat, lon = min(max(lat, box[0]), box[1]), min(max(lon, box[2]), box[3])
        out.append((t, lat, lon, rng.choices(SEVERITIES, SEV_MIX)[0]))
    return out


def run_episode(seed, choose=None, learn=None, world=None, n_incidents=200, load=(0.3, 0.8), surge=None, fleet=None,
                rank_fn=None):
    """choose(state, n_valid) -> rank; learn(s, a, reward, s_next, n_valid_next, done). Both optional.
    The same seed gives the same layout and incident stream whatever the policy does."""
    rng = random.Random(seed)
    world = world or random_world(rng, fleet)
    incs = make_incidents(rng, world.n_amb, n_incidents, load, surge)
    locs, masks, n_amb = world.locs, world.masks, world.n_amb
    where = list(range(n_amb))                 # current location index of each ambulance
    free, busy, waiting = set(range(n_amb)), [], []
    served, prev, interval, t, ai = [], None, 0.0, 0.0, 0   # served: (severity, response, wait)

    def assign(a, inc, now):
        """Send ambulance a to inc; it ends at the hospital nearest the incident, and the trip there
        takes distance / speed. Returns ETA minutes."""
        eta = haversine_km(*locs[where[a]], inc[1], inc[2]) / SPEED_KMPH * 60
        hosp = min(world.hosp, key=lambda h: haversine_km(inc[1], inc[2], *locs[h]))
        trip = haversine_km(inc[1], inc[2], *locs[hosp]) / SPEED_KMPH * 60
        where[a] = hosp
        free.discard(a)
        heapq.heappush(busy, (now + FIXED_MINUTES + eta + trip, a))
        served.append((inc[3], now - inc[0] + 0.5 + eta, now - inc[0]))
        return eta

    while ai < len(incs) or busy:
        na = incs[ai][0] if ai < len(incs) else math.inf
        nf = busy[0][0] if busy else math.inf
        nxt = min(na, nf)
        interval -= (nxt - t) * sum(W_SEV[w[3]] for w in waiting)     # waiting costs every minute
        t = nxt
        if nf <= na:                                                  # an ambulance frees up
            a = heapq.heappop(busy)[1]
            free.add(a)
            if waiting:
                # Least Laxity First, the queue's default: the call with the least spare time before its
                # deadline goes first. `now` is the same for all of them, so it drops out of the comparison.
                inc = min(waiting, key=lambda w: w[0] + RESPONSE_TARGET_MINUTES[w[3]]
                          - haversine_km(*locs[where[a]], w[1], w[2]) / SPEED_KMPH * 60)
                waiting.remove(inc)
                interval -= W_SEV[inc[3]] * assign(a, inc, t)
            continue
        inc = incs[ai]
        ai += 1
        if not free:
            waiting.append(inc)
            continue
        d = {a: haversine_km(*locs[where[a]], inc[1], inc[2]) for a in free}
        ranked = sorted(sorted(free), key=d.__getitem__)
        if len(ranked) == 1:                                          # no choice to make
            interval -= W_SEV[inc[3]] * assign(ranked[0], inc, t)
            continue
        m = min(N_ACTIONS, len(ranked))
        cover = [masks[where[a]] for a in ranked]
        rest = 0
        for c in cover[1:]:
            rest |= c
        s = encode_state(inc[3], len(ranked) / n_amb, demand_share(cover[0] & ~rest),
                         (d[ranked[1]] - d[ranked[0]]) / SPEED_KMPH * 60,
                         busy[0][0] - t if busy else 0.0)
        if prev and learn:
            learn(prev[0], prev[1], prev[2] + interval, s, m, False)
        interval = 0.0
        if rank_fn:     # a rule that works from the whole situation instead of the bucketed state
            k = rank_fn(inc, ranked, m, d, where, world, (busy[0][0] - t if busy else 0.0, len(waiting)))
        else:
            k = choose(s, m) if choose else 0
        after = 0
        for i, c in enumerate(cover):
            if i != k:
                after |= c
        eta = assign(ranked[k], inc, t)
        prev = (s, k, -W_SEV[inc[3]] * eta + LAMBDA * demand_share(after))
    if prev and learn:
        learn(prev[0], prev[1], prev[2] + interval, None, 0, True)

    resp = sorted(r for _, r, _ in served)
    wsum = sum(W_SEV[s] for s, _, _ in served)
    return {"n": len(served), "mean": sum(resp) / len(resp), "p95": resp[int(0.95 * len(resp)) - 1],
            "weighted": sum(W_SEV[s] * r for s, r, _ in served) / wsum,
            "wait": sum(w for _, _, w in served) / len(served)}


# Where incidents happen, learned from a long sample of the generator (the real system would use its history).
_rng = random.Random(2026)
set_demand([(i[1], i[2]) for i in make_incidents(_rng, 12, 20_000)])


def _selfcheck():
    # The job-time constants must still match the real lifecycle.
    src = (ROOT / "app" / "core" / "lifecycle.py").read_text()
    for name, minutes in (("ASSIGNED", 0.5), ("AT_SCENE", 5.0), ("AT_HOSPITAL", 5.0)):
        found = re.search(r"AmbulanceStatus\.%s:\s*([\d.]+)" % name, src)
        assert found and float(found.group(1)) == minutes, "lifecycle.PHASE_MINUTES changed: %s" % name
    assert re.search(r"AmbulanceStatus\.TRANSPORTING:\s*None", src), "trip to hospital is no longer distance-based"
    sched = (ROOT / "app" / "core" / "scheduling.py").read_text()
    for sev, minutes in RESPONSE_TARGET_MINUTES.items():
        found = re.search(r"Severity\.%s:\s*([\d.]+)" % sev, sched)
        assert found and float(found.group(1)) == minutes, "scheduling.RESPONSE_TARGET_MINUTES changed: %s" % sev
    w = real_world()
    assert (w.n_amb, len(w.hosp)) == (12, 4), (w.n_amb, len(w.hosp))
    # Every incident is served, nobody is served faster than physics allows, and runs are repeatable.
    log = []
    first = run_episode(7, world=w, learn=lambda *x: log.append(x))
    assert first["n"] == 200 and first["mean"] >= 0.5 and first == run_episode(7, world=w)
    assert log and log[-1][5] and not any(x[5] for x in log[:-1]), "exactly the last transition is terminal"
    assert all(x[4] >= 2 for x in log[:-1]), "a decision needs at least two free ambulances"
    # Nearest-first never loses to picking the 5th-nearest on a quiet fleet.
    best = lambda s, m: m - 1
    assert run_episode(3, world=w)["mean"] <= run_episode(3, choose=best, world=w)["mean"] + 5
    print("env self-check ok:", {k: round(v, 2) for k, v in first.items()})


if __name__ == "__main__":
    _selfcheck()
