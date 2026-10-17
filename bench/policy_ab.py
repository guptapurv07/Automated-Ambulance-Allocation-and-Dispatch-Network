"""A/B on the REAL system: nearest ambulance vs the lookahead rule vs the learned (RL) policy, same streams.

Same machinery as bench/scheduling.py (API, worker pool, row locks, MySQL, simulation clock). For each seed
one stream of calls is sent once per policy. Before each run the incidents table is seeded with past
incidents drawn from the same hotspots, because both policies learn where incidents happen from that table.
The calls come from the simulator's generator (rl/env.py), so both use the same city.

    python -m bench.policy_ab --seeds 6
    python -m bench.policy_ab --seeds 20 --policies greedy learned
"""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Incident, IncidentStatus, IncidentType, Severity
from bench.loadtest import reset_database
from sim.seed import STATIONS

from rl.env import BACKGROUND, HOTSPOTS, SEV_MIX, SEVERITIES, SPREAD_DEG, W_SEV, city_box

POLICIES = ("greedy", "lookahead", "learned")
SERVED = re.compile(r"incident=(\d+) SERVED\s+severity=(\w+) waited=([0-9.]+)min trip=([0-9.]+)min response=([0-9.]+)min")


def point(rng):
    box = city_box()
    k = rng.choices(range(len(HOTSPOTS) + 1), [h[2] for h in HOTSPOTS] + [BACKGROUND])[0]
    if k < len(HOTSPOTS):
        lat, lon = rng.gauss(HOTSPOTS[k][0], SPREAD_DEG), rng.gauss(HOTSPOTS[k][1], SPREAD_DEG)
    else:
        lat, lon = rng.uniform(box[0], box[1]), rng.uniform(box[2], box[3])
    return min(max(lat, box[0]), box[1]), min(max(lon, box[2]), box[3])


def stream(seed, calls, minutes):
    rng = random.Random(seed)
    out = []
    for n in range(calls):
        lat, lon = point(rng)
        out.append((rng.uniform(0, minutes), {"lat": lat, "lon": lon,
                    "severity": rng.choices(SEVERITIES, SEV_MIX)[0],
                    "description": "call %d" % (n + 1)}))
    return sorted(out, key=lambda c: c[0])


def seed_history(n, seed):
    rng = random.Random(seed + 9000)
    engine = create_engine(get_settings().database_url)
    with Session(engine) as session:
        for _ in range(n):
            lat, lon = point(rng)
            session.add(Incident(lat=lat, lon=lon, severity=Severity.MEDIUM, status=IncidentStatus.RESOLVED,
                                 incident_type=IncidentType.OTHER,
                                 reported_at=datetime.now() - timedelta(days=rng.uniform(1, 30))))
        session.commit()
    engine.dispose()


def count_dispatches():
    engine = create_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("SELECT COUNT(*) FROM dispatches")).scalar_one())
    finally:
        engine.dispose()


def run(policy, beta, calls_stream, scale, port, history, seed):
    reset_database()
    seed_history(history, seed)
    log = tempfile.NamedTemporaryFile(mode="w+", suffix=".ab.log", delete=False)
    env = {**os.environ, "DISPATCH_POLICY": policy, "LOOKAHEAD_BETA": str(beta), "QUEUE_SCHEDULER": "llf",
           "SIM_CLOCK_ENABLED": "true", "SIM_TIME_SCALE": str(scale), "SIM_TICK_SECONDS": "0.2",
           "WORKER_POOL_SIZE": "4", "LOCKING_MODE": "for_update", "RACE_WINDOW_MS": "0", "DEDUP_ENABLED": "false"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
                            env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
    base = "http://127.0.0.1:%d" % port
    try:
        for _ in range(60):
            try:
                if httpx.get(base + "/health", timeout=1).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("server did not start")
        start = time.time()
        with ThreadPoolExecutor(max_workers=16) as pool:
            for minute, body in calls_stream:
                wait = start + minute * 60.0 / scale - time.time()
                if wait > 0:
                    time.sleep(wait)
                pool.submit(httpx.post, base + "/incidents", json=body, timeout=60)
        deadline = time.time() + 300
        while time.time() < deadline and count_dispatches() < len(calls_stream):
            time.sleep(0.5)
    finally:
        proc.terminate()
        proc.wait(timeout=15)
        log.close()
    rows = [(m.group(2), float(m.group(3)), float(m.group(4)), float(m.group(5)))
            for m in SERVED.finditer(open(log.name).read())]
    os.unlink(log.name)
    return rows


def summary(rows):
    w = sum(W_SEV[s] for s, *_ in rows)
    return {"n": len(rows), "weighted": sum(W_SEV[s] * r for s, _, _, r in rows) / w,
            "mean": sum(r for *_, r in rows) / len(rows), "wait": sum(x for _, x, _, _ in rows) / len(rows),
            "trip": sum(t for _, _, t, _ in rows) / len(rows)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", type=int, default=6)
    p.add_argument("--first", type=int, default=0, help="first stream number, to add new streams to an earlier run")
    p.add_argument("--calls", type=int, default=5 * len(STATIONS),
                   help="calls per stream (default 5 per ambulance, so the fleet is about as busy whatever its size)")
    p.add_argument("--minutes", type=float, default=200.0)
    p.add_argument("--scale", type=float, default=300.0)
    p.add_argument("--beta", type=float, default=1.25)
    p.add_argument("--history", type=int, default=600)
    p.add_argument("--port", type=int, default=8123)
    p.add_argument("--policies", nargs="+", default=list(POLICIES), choices=POLICIES)
    a = p.parse_args()
    print("\n%d calls over %.0f simulated minutes, %d ambulances, time x%.0f, %d past incidents seeded, %d streams\n"
          % (a.calls, a.minutes, len(STATIONS), a.scale, a.history, a.seeds))
    print("  %-5s %-10s %5s %9s %9s %8s %8s" % ("seed", "policy", "served", "weighted", "mean", "wait", "trip"))
    res = {policy: [] for policy in a.policies}
    for seed in range(a.first, a.first + a.seeds):
        st = stream(5000 + seed, a.calls, a.minutes)
        for policy in a.policies:
            r = summary(run(policy, a.beta, st, a.scale, a.port, a.history, seed))
            res[policy].append(r)
            print("  %-5d %-10s %6d %9.2f %9.2f %8.2f %8.2f" % (seed, policy, r["n"], r["weighted"], r["mean"], r["wait"], r["trip"]), flush=True)
    print()
    pairs = [(x, "greedy") for x in a.policies if x != "greedy" and "greedy" in a.policies]
    if "lookahead" in a.policies and "learned" in a.policies:
        pairs.append(("learned", "lookahead"))
    for x, y in pairs:
        diffs = [p["weighted"] - q["weighted"] for p, q in zip(res[x], res[y])]
        trips = [p["trip"] - q["trip"] for p, q in zip(res[x], res[y])]
        mu = sum(diffs) / len(diffs)
        se = math.sqrt(sum((d - mu) ** 2 for d in diffs) / (len(diffs) - 1) / len(diffs)) if len(diffs) > 1 else float("nan")
        print("%-9s minus %-9s weighted response %+.2f min (95%% CI %+.2f .. %+.2f, %d streams, %d better)  trip %+.2f min"
              % (x, y, mu, mu - 1.96 * se, mu + 1.96 * se, len(diffs), sum(d < 0 for d in diffs), sum(trips) / len(trips)))

if __name__ == "__main__":
    main()
