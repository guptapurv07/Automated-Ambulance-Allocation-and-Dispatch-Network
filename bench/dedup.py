"""Benchmark: do repeat reports of one emergency waste ambulances?

Simulates several accidents. Each one is reported at the same instant by
several bystanders standing a few metres apart, which is the situation the
project started from. The scenario runs twice against a freshly seeded
database: once with the deduplication gate off, once with it on.

    python -m bench.dedup
    python -m bench.dedup --accidents 8 --callers 4

The accident sites sit exactly on grid-cell edges on purpose, so the callers
of one accident fall into different cells. That is the hard case for the gate:
reports that are simultaneous and also split across a cell boundary.

Row locking is left on in both runs. It is not what is being measured, and it
does not help here: with the gate off no ambulance is double-booked, and
vehicles are wasted all the same.
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.core.dedup import dedup_key
from app.models import IncidentType
from bench.loadtest import reset_database

# (place, latitude, longitude). Coordinates are multiples of the cell size, so
# every site lies on a cell edge.
SITES: list[tuple[str, float, float]] = [
    ("Haridwar Bypass", 30.2740, 78.0480),
    ("Mussoorie Road", 30.3980, 78.0720),
    ("Paltan Bazaar", 30.3220, 78.0330),
    ("Rajpur Road", 30.3450, 78.0560),
    ("Doon University", 30.2830, 78.0620),
    ("Prem Nagar", 30.3320, 77.9410),
    ("ISBT", 30.2890, 77.9960),
    ("Sahastradhara", 30.3870, 78.1210),
]

# How far a bystander stands from the accident, in degrees (about 30 m).
CALLER_SCATTER = 0.0003


@dataclass
class Result:
    gate: str
    reports: int
    merged: int
    incidents: int
    sent: int
    served: int
    accidents: int
    free: int
    keys_checked: int
    keys_wrong: int
    wall_seconds: float

    @property
    def extra(self) -> int:
        """Ambulances sent beyond one per accident."""
        return self.sent - self.served

    @property
    def unserved(self) -> int:
        return self.accidents - self.served


def collect(accidents: int) -> dict[str, int]:
    mav_engine = create_engine(get_settings().database_url)
    try:
        with mav_engine.connect() as mav_conn:
            def scalar(sql: str) -> int:
                return int(mav_conn.execute(text(sql)).scalar_one())

            mav_keyed = mav_conn.execute(text(
                "SELECT lat, lon, incident_type, reported_at, dedup_key "
                "FROM incidents WHERE dedup_key IS NOT NULL"
            )).fetchall()
            # A stored key must be reproducible from the row's own stored
            # fields. This is what the DOUBLE coordinates and whole-second
            # timestamps are for.
            mav_wrong = sum(
                1 for mav_row in mav_keyed
                if dedup_key(mav_row[0], mav_row[1],
                             IncidentType(mav_row[2]), mav_row[3]) != mav_row[4]
            )
            return {
                "incidents": scalar("SELECT COUNT(*) FROM incidents"),
                "sent": scalar("SELECT COUNT(*) FROM dispatches"),
                # Each accident's reports share one description.
                "served": scalar(
                    "SELECT COUNT(DISTINCT i.description) FROM incidents i "
                    "JOIN dispatches d ON d.incident_id = i.id"
                ),
                "free": scalar(
                    "SELECT COUNT(*) FROM ambulances WHERE status = 'AVAILABLE'"
                ),
                "keys_checked": len(mav_keyed),
                "keys_wrong": mav_wrong,
                "accidents": accidents,
            }
    finally:
        mav_engine.dispose()


def run(gate_on: bool, accidents: int, callers: int, port: int) -> Result:
    mav_label = "on" if gate_on else "off"
    print(f"  gate {mav_label:<4} ", end="", flush=True)
    reset_database()

    mav_log = tempfile.NamedTemporaryFile(mode="w+", suffix=".dedup.log", delete=False)
    mav_env = {
        **os.environ,
        "DEDUP_ENABLED": "true" if gate_on else "false",
        "LOCKING_MODE": "for_update",
        "RACE_WINDOW_MS": "0",
        "WORKER_POOL_SIZE": "4",
        "SIM_CLOCK_ENABLED": "false",   # no recycling and no retries mid-run
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
            mav_log.flush()
            mav_tail = "".join(open(mav_log.name).readlines()[-5:])
            raise RuntimeError(
                f"server did not start on port {port}; try --port. "
                f"Its last output:\n{mav_tail}"
            )

        random.seed(2026)  # identical callers in both runs
        mav_reports = [
            {
                "lat": mav_lat + random.uniform(-CALLER_SCATTER, CALLER_SCATTER),
                "lon": mav_lon + random.uniform(-CALLER_SCATTER, CALLER_SCATTER),
                "severity": "CRITICAL",
                "incident_type": "ROAD_ACCIDENT",
                "description": f"Road accident at {mav_place}",
            }
            for mav_place, mav_lat, mav_lon in SITES[:accidents]
            for _ in range(callers)
        ]
        random.shuffle(mav_reports)

        mav_start = time.time()
        with ThreadPoolExecutor(max_workers=len(mav_reports)) as mav_pool:
            mav_responses = list(mav_pool.map(
                lambda mav_r: httpx.post(f"{mav_base}/incidents", json=mav_r, timeout=60),
                mav_reports,
            ))
        mav_bad = [mav_r.status_code for mav_r in mav_responses if mav_r.status_code != 202]
        if mav_bad:
            raise RuntimeError(f"intake returned errors: {mav_bad}")

        for _ in range(240):  # wait until every queued incident has been handled
            mav_stats = httpx.get(f"{mav_base}/queue", timeout=5).json()
            mav_done = mav_stats["workers"]["handled"] + mav_stats["workers"]["failed"]
            if (mav_stats["queue"]["depth"] == 0
                    and mav_done >= mav_stats["queue"]["enqueued_total"]):
                break
            time.sleep(0.25)
        mav_wall = time.time() - mav_start
    finally:
        mav_proc.terminate()
        mav_proc.wait(timeout=15)
        mav_log.close()
        os.unlink(mav_log.name)

    print(f"done  ({mav_wall:.1f}s)")
    return Result(
        gate=mav_label,
        reports=len(mav_reports),
        merged=sum(1 for mav_r in mav_responses if mav_r.json()["duplicate"]),
        wall_seconds=mav_wall,
        **collect(accidents),
    )


def print_report(results: list[Result], accidents: int, callers: int) -> None:
    print("\n" + "=" * 78)
    print(f"  {accidents} accidents  |  {callers} callers each, all at the same instant  "
          f"|  12 ambulances")
    print("=" * 78)
    print(f"\n  {'gate':<6}{'reports':>9}{'merged':>8}{'incidents':>11}"
          f"{'sent':>6}{'EXTRA':>7}{'unserved':>10}{'still free':>12}")
    print("  " + "-" * 74)
    for mav_r in results:
        print(f"  {mav_r.gate:<6}{mav_r.reports:>9}{mav_r.merged:>8}{mav_r.incidents:>11}"
              f"{mav_r.sent:>6}{mav_r.extra:>7}{mav_r.unserved:>10}{mav_r.free:>12}")
    print("\n  sent = ambulances dispatched   EXTRA = sent beyond one per accident")
    print("  unserved = accidents that got no ambulance at all")

    print("\n" + "-" * 78)
    mav_off = next((mav_r for mav_r in results if mav_r.gate == "off"), None)
    mav_on = next((mav_r for mav_r in results if mav_r.gate == "on"), None)
    if mav_off:
        print(f"  Gate off: {mav_off.accidents} accidents took {mav_off.sent} ambulances, "
              f"{mav_off.extra} of them unnecessary; {mav_off.free} left for anyone else.")
    if mav_on:
        print(f"  Gate on : {mav_on.accidents} accidents took {mav_on.sent} ambulances, "
              f"{mav_on.extra} unnecessary; {mav_on.free} left for anyone else.")
        print(f"  Stored keys recomputed from their own rows: "
              f"{mav_on.keys_checked - mav_on.keys_wrong}/{mav_on.keys_checked} match.")
    print("-" * 78 + "\n")


def main() -> int:
    mav_parser = argparse.ArgumentParser(description=__doc__)
    mav_parser.add_argument("--accidents", type=int, default=6,
                            choices=range(1, len(SITES) + 1), metavar=f"1..{len(SITES)}")
    mav_parser.add_argument("--callers", type=int, default=5)
    mav_parser.add_argument("--port", type=int, default=8022)
    mav_parser.add_argument("--keep", action="store_true",
                            help="leave the last run's data in place for inspection")
    mav_args = mav_parser.parse_args()

    print(f"\n{mav_args.accidents} accidents, {mav_args.callers} callers each "
          f"({mav_args.accidents * mav_args.callers} reports)\n")
    mav_results = [
        run(mav_gate, mav_args.accidents, mav_args.callers, mav_args.port)
        for mav_gate in (False, True)
    ]
    print_report(mav_results, mav_args.accidents, mav_args.callers)
    if mav_args.keep:
        print("  --keep: the gate-on run's data is left in the database.\n")
    else:
        reset_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
