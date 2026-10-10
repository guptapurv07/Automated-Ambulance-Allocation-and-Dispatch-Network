"""Benchmark: does row-level locking actually prevent misallocation?

Fires an identical burst of simultaneous emergency calls at the system under
each locking mode and reports what changes. Every call is clustered in a small
area, so each one ranks the same handful of vehicles as nearest -- that is what
forces workers to contend for the same rows instead of spreading out.

    python -m bench.loadtest
    python -m bench.loadtest --calls 60 --workers 8 --race-window 25

Each mode runs against a freshly seeded database in its own server process, so
the runs cannot contaminate one another.

A note on --race-window: the gap between reading a vehicle as available and
writing the claim is naturally microseconds wide, so an unsafe baseline only
corrupts occasionally. This widens that gap deliberately. It is applied in
EVERY mode, not just the unsafe one -- under locking the rows are already held,
so the pause costs latency and nothing else. That keeps the comparison honest.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from sqlalchemy import create_engine, text

from app.config import get_settings

# All calls land within ~500m of the Clock Tower.
CLUSTER_LAT, CLUSTER_LON, CLUSTER_SPREAD = 30.3255, 78.0413, 0.004
MODES = ("none", "for_update", "skip_locked")


@dataclass
class Result:
    mode: str
    calls: int
    dispatched: int
    distinct_vehicles: int
    double_booked: int
    unassigned: int
    wall_seconds: float
    lock_wait_median: float
    lock_wait_max: float
    deadlocks: int

    @property
    def throughput(self) -> float:
        return self.calls / self.wall_seconds if self.wall_seconds else 0.0

    @property
    def verdict(self) -> str:
        return "CORRUPTED" if self.double_booked else "safe"


def reset_database() -> None:
    subprocess.run(
        [sys.executable, "-m", "sim.seed", "--reset"],
        check=True, capture_output=True,
    )


def collect_from_database() -> dict[str, int]:
    mav_engine = create_engine(get_settings().database_url)
    try:
        with mav_engine.connect() as mav_conn:
            def scalar(sql: str) -> int:
                return int(mav_conn.execute(text(sql)).scalar_one())

            return {
                "dispatched": scalar("SELECT COUNT(*) FROM dispatches"),
                "distinct_vehicles": scalar(
                    "SELECT COUNT(DISTINCT ambulance_id) FROM dispatches"
                ),
                # The headline number: a vehicle holding more than one live
                # dispatch is an ambulance sent to two emergencies at once.
                "double_booked": scalar(
                    "SELECT COUNT(*) FROM ("
                    "  SELECT ambulance_id FROM dispatches WHERE status='ACTIVE'"
                    "  GROUP BY ambulance_id HAVING COUNT(*) > 1"
                    ") AS offenders"
                ),
                "unassigned": scalar(
                    "SELECT COUNT(*) FROM incidents WHERE status='UNASSIGNED'"
                ),
            }
    finally:
        mav_engine.dispose()


def run_mode(mode: str, calls: int, workers: int, race_window: float,
             port: int) -> Result:
    print(f"  {mode:<12} ", end="", flush=True)
    reset_database()

    mav_log = tempfile.NamedTemporaryFile(
        mode="w+", suffix=f".{mode}.log", delete=False
    )
    mav_env = {
        **os.environ,
        "LOCKING_MODE": mode,
        "WORKER_POOL_SIZE": str(workers),
        "RACE_WINDOW_MS": str(race_window),
        "SIM_CLOCK_ENABLED": "false",   # vehicles must not recycle mid-run
        # These calls stand for 40 different emergencies that happen to be
        # close together. The deduplication gate would merge them, so it is
        # switched off here; bench/dedup.py measures that gate on its own.
        "DEDUP_ENABLED": "false",
    }
    mav_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--port", str(port), "--log-level", "warning"],
        env=mav_env, stdout=mav_log, stderr=subprocess.STDOUT, text=True,
    )
    mav_base = f"http://127.0.0.1:{port}"

    try:
        for _ in range(60):
            try:
                if httpx.get(f"{mav_base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError(f"server for mode={mode} never started")

        random.seed(1234)  # identical call pattern in every mode
        mav_calls = [
            {
                "lat": CLUSTER_LAT + random.uniform(-CLUSTER_SPREAD, CLUSTER_SPREAD),
                "lon": CLUSTER_LON + random.uniform(-CLUSTER_SPREAD, CLUSTER_SPREAD),
                "severity": "CRITICAL",
                "description": f"burst call {i}",
            }
            for i in range(calls)
        ]

        mav_start = time.time()
        with ThreadPoolExecutor(max_workers=calls) as mav_pool:
            list(mav_pool.map(
                lambda c: httpx.post(f"{mav_base}/incidents", json=c, timeout=60),
                mav_calls,
            ))

        for _ in range(240):  # wait for the queue to drain
            mav_stats = httpx.get(f"{mav_base}/queue", timeout=5).json()
            if (mav_stats["queue"]["depth"] == 0
                    and mav_stats["workers"]["handled"] >= calls):
                break
            time.sleep(0.25)
        mav_wall = time.time() - mav_start
    finally:
        mav_proc.terminate()
        mav_proc.wait(timeout=15)
        mav_log.close()

    mav_text = open(mav_log.name).read()
    mav_waits = [float(w) for w in re.findall(r"lock_wait=([0-9.]+)ms", mav_text)]
    mav_deadlocks = len(re.findall(r"[Dd]eadlock|\b1213\b", mav_text))
    os.unlink(mav_log.name)

    mav_db = collect_from_database()
    print(f"done  ({mav_wall:.1f}s)")
    return Result(
        mode=mode, calls=calls, wall_seconds=mav_wall,
        lock_wait_median=statistics.median(mav_waits) if mav_waits else 0.0,
        lock_wait_max=max(mav_waits) if mav_waits else 0.0,
        deadlocks=mav_deadlocks, **mav_db,
    )


def print_report(results: list[Result], calls: int, workers: int,
                 race_window: float) -> None:
    print("\n" + "=" * 78)
    print(f"  {calls} simultaneous calls  |  {workers} workers  |  "
          f"race window {race_window}ms")
    print("=" * 78)
    print(f"\n  {'mode':<12}{'disp':>6}{'vehicles':>10}{'DOUBLE-BOOKED':>15}"
          f"{'unassigned':>12}{'verdict':>11}")
    print("  " + "-" * 74)
    for r in results:
        print(f"  {r.mode:<12}{r.dispatched:>6}{r.distinct_vehicles:>10}"
              f"{r.double_booked:>15}{r.unassigned:>12}{r.verdict:>11}")

    print(f"\n  {'mode':<12}{'wall':>9}{'calls/s':>10}{'lock p50':>11}"
          f"{'lock max':>11}{'deadlocks':>11}")
    print("  " + "-" * 74)
    for r in results:
        print(f"  {r.mode:<12}{r.wall_seconds:>8.1f}s{r.throughput:>10.1f}"
              f"{r.lock_wait_median:>10.1f}ms{r.lock_wait_max:>10.1f}ms"
              f"{r.deadlocks:>11}")

    print("\n" + "-" * 78)
    mav_unsafe = next((r for r in results if r.mode == "none"), None)
    mav_safe = [r for r in results if r.mode != "none"]
    if mav_unsafe and mav_safe:
        if mav_unsafe.double_booked and all(r.double_booked == 0 for r in mav_safe):
            print(f"  RESULT: without locking, {mav_unsafe.double_booked} "
                  f"ambulance(s) were dispatched to more than one emergency.")
            print("          With row-level locking, zero. The locking is doing "
                  "the work.")
        elif not mav_unsafe.double_booked:
            print("  RESULT: the unsafe baseline did not corrupt this run. "
                  "Raise --race-window")
            print("          or --calls to widen the window and try again.")
    print("-" * 78 + "\n")


def main() -> int:
    mav_parser = argparse.ArgumentParser(description=__doc__)
    mav_parser.add_argument("--calls", type=int, default=40)
    mav_parser.add_argument("--workers", type=int, default=8)
    mav_parser.add_argument("--race-window", type=float, default=20.0,
                            help="milliseconds between read and claim")
    mav_parser.add_argument("--modes", nargs="+", default=list(MODES),
                            choices=MODES)
    mav_parser.add_argument("--port", type=int, default=8020)
    mav_parser.add_argument("--keep", action="store_true",
                            help="leave the last run's data in place for inspection")
    mav_args = mav_parser.parse_args()

    print(f"\nRunning {len(mav_args.modes)} mode(s), "
          f"{mav_args.calls} concurrent calls each\n")
    mav_results = [
        run_mode(m, mav_args.calls, mav_args.workers,
                 mav_args.race_window, mav_args.port)
        for m in mav_args.modes
    ]
    print_report(mav_results, mav_args.calls, mav_args.workers,
                 mav_args.race_window)
    if mav_args.keep:
        print("  --keep: last run's data left in the database for inspection.\n")
    else:
        reset_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
