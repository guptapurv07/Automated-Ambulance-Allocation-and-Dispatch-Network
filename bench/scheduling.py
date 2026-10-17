"""Benchmark: does the scheduler change who gets help in time?

Sends the same stream of mixed-severity emergency calls to the system three
times, once for each scheduler (FIFO, priority, Least Laxity First). For part
of the run there are more calls than free ambulances, so calls have to wait,
and the scheduler decides which waiting call gets the next free ambulance.

    python -m bench.scheduling
    python -m bench.scheduling --calls 60 --minutes 60

Two things are reported for each severity. WAIT is how long a call waited
before an ambulance was assigned to it; this is the part the scheduler
controls. LATE counts the calls where the ambulance reached the patient after
the response target (CRITICAL 8, HIGH 15, MEDIUM 30, LOW 60 minutes). Being
late also depends on the length of the trip, which no scheduler can change.

The calls come from the central part of the city, where most ambulance
stations are. Trips there are short, so whether a call is late depends on how
long it waited for a vehicle, which is the part the scheduler controls. With
calls spread over the whole district many would be late on distance alone,
whatever the scheduler did.

Times are simulated minutes. The simulation clock is on, so ambulances finish
their calls and come back into service during the run. Time is compressed
(300x by default) to keep the run short.

Keep the laptop awake and its lid open while this runs. Response times are
measured on the real clock, so a machine that sleeps mid-run produces long,
meaningless waits.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx
from sqlalchemy import create_engine, text

from app.config import get_settings
from bench.loadtest import reset_database
from sim.seed import STATIONS

SCHEDULERS = ("fifo", "priority", "llf")
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
# Share of each severity in the stream of calls.
SEVERITY_MIX = (0.20, 0.30, 0.30, 0.20)
TARGETS = {"CRITICAL": 8, "HIGH": 15, "MEDIUM": 30, "LOW": 60}
# The central city: roughly 8 km by 8 km around the Clock Tower.
CITY_CENTRE_LAT = (30.29, 30.36)
CITY_CENTRE_LON = (77.99, 78.07)

SERVED_LINE = re.compile(
    r"incident=(\d+) SERVED\s+severity=(\w+) waited=([0-9.]+)min trip=([0-9.]+)min "
    r"response=([0-9.]+)min target=\d+min (ON TIME|LATE)"
)


@dataclass
class Result:
    scheduler: str
    wall_seconds: float
    responses: dict[str, list[float]] = field(default_factory=dict)
    waits: dict[str, list[float]] = field(default_factory=dict)
    trips: list[float] = field(default_factory=list)
    late: dict[str, int] = field(default_factory=dict)

    def count(self, severity: str) -> int:
        return len(self.responses.get(severity, []))

    def mean_wait(self, severity: str) -> float:
        mav_values = self.waits.get(severity, [])
        return sum(mav_values) / len(mav_values) if mav_values else 0.0

    @property
    def mean_trip(self) -> float:
        return sum(self.trips) / len(self.trips) if self.trips else 0.0

    @property
    def total_late(self) -> int:
        return sum(self.late.values())

    @property
    def total(self) -> int:
        return sum(len(mav_v) for mav_v in self.responses.values())

    @property
    def worst_overdue(self) -> float:
        """How far past its own target the worst-served call was, in minutes."""
        return max(
            (mav_r - TARGETS[mav_s]
             for mav_s, mav_values in self.responses.items() for mav_r in mav_values),
            default=0.0,
        )


def build_calls(calls: int, minutes: float) -> list[tuple[float, dict]]:
    """The stream of calls: (arrival time in simulated minutes, request body)."""
    mav_rng = random.Random(606)   # the same stream for every scheduler
    mav_stream = []
    for mav_n in range(calls):
        mav_severity = mav_rng.choices(SEVERITIES, weights=SEVERITY_MIX)[0]
        mav_stream.append((
            mav_rng.uniform(0, minutes),
            {
                "lat": mav_rng.uniform(*CITY_CENTRE_LAT),
                "lon": mav_rng.uniform(*CITY_CENTRE_LON),
                "severity": mav_severity,
                "description": f"call {mav_n + 1}",
            },
        ))
    return sorted(mav_stream, key=lambda mav_c: mav_c[0])


def count_dispatches() -> int:
    mav_engine = create_engine(get_settings().database_url)
    try:
        with mav_engine.connect() as mav_conn:
            return int(mav_conn.execute(text("SELECT COUNT(*) FROM dispatches")).scalar_one())
    finally:
        mav_engine.dispose()


def run(scheduler: str, stream: list[tuple[float, dict]], scale: float, port: int) -> Result:
    print(f"  {scheduler:<9} ", end="", flush=True)
    reset_database()

    mav_log = tempfile.NamedTemporaryFile(mode="w+", suffix=f".{scheduler}.log", delete=False)
    mav_env = {
        **os.environ,
        "QUEUE_SCHEDULER": scheduler,
        "SIM_CLOCK_ENABLED": "true",
        "SIM_TIME_SCALE": str(scale),
        "SIM_TICK_SECONDS": "0.2",
        "WORKER_POOL_SIZE": "4",
        "LOCKING_MODE": "for_update",
        "RACE_WINDOW_MS": "0",
        # Every call here is a different emergency, so nothing should be merged.
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
            mav_log.flush()
            mav_tail = "".join(open(mav_log.name).readlines()[-5:])
            raise RuntimeError(
                f"server did not start on port {port}; try --port. "
                f"Its last output:\n{mav_tail}"
            )

        mav_start = time.time()
        with ThreadPoolExecutor(max_workers=16) as mav_pool:
            for mav_minute, mav_body in stream:
                # Send each call at its own moment, in simulated time.
                mav_due = mav_start + mav_minute * 60.0 / scale
                mav_wait = mav_due - time.time()
                if mav_wait > 0:
                    time.sleep(mav_wait)
                mav_pool.submit(httpx.post, f"{mav_base}/incidents", json=mav_body, timeout=60)

        # Every call has been sent. Wait until each one has an ambulance.
        mav_deadline = time.time() + 300
        while time.time() < mav_deadline and count_dispatches() < len(stream):
            time.sleep(0.5)
        mav_wall = time.time() - mav_start
    finally:
        mav_proc.terminate()
        mav_proc.wait(timeout=15)
        mav_log.close()

    mav_result = Result(scheduler=scheduler, wall_seconds=mav_wall)
    for mav_match in SERVED_LINE.finditer(open(mav_log.name).read()):
        mav_severity = mav_match.group(2)
        mav_result.waits.setdefault(mav_severity, []).append(float(mav_match.group(3)))
        mav_result.trips.append(float(mav_match.group(4)))
        mav_result.responses.setdefault(mav_severity, []).append(float(mav_match.group(5)))
        if mav_match.group(6) == "LATE":
            mav_result.late[mav_severity] = mav_result.late.get(mav_severity, 0) + 1
    os.unlink(mav_log.name)

    print(f"done  ({mav_wall:.0f}s, {mav_result.total} of {len(stream)} calls served)")
    return mav_result


def print_report(results: list[Result], calls: int, minutes: float, scale: float) -> None:
    print("\n" + "=" * 78)
    print(f"  {calls} calls over {minutes:.0f} simulated minutes  |  {len(STATIONS)} ambulances  "
          f"|  time x{scale:.0f}")
    print("=" * 78)

    print("\n  Average WAIT for an ambulance, in minutes (what the scheduler controls)\n")
    print(f"  {'scheduler':<11}" + "".join(f"{mav_s:>11}" for mav_s in SEVERITIES)
          + f"{'avg trip':>11}")
    print("  " + "-" * 66)
    for mav_r in results:
        mav_cells = "".join(f"{mav_r.mean_wait(mav_s):>11.1f}" for mav_s in SEVERITIES)
        print(f"  {mav_r.scheduler:<11}{mav_cells}{mav_r.mean_trip:>11.1f}")

    print("\n  LATE calls: ambulance reached the patient after the target "
          "(" + ", ".join(f"{mav_s} {TARGETS[mav_s]}" for mav_s in SEVERITIES) + " min)\n")
    print(f"  {'scheduler':<11}" + "".join(f"{mav_s:>11}" for mav_s in SEVERITIES)
          + f"{'TOTAL':>10}{'worst overdue':>16}")
    print("  " + "-" * 81)
    for mav_r in results:
        mav_cells = "".join(
            f"{str(mav_r.late.get(mav_s, 0)) + '/' + str(mav_r.count(mav_s)):>11}"
            for mav_s in SEVERITIES
        )
        print(f"  {mav_r.scheduler:<11}{mav_cells}"
              f"{str(mav_r.total_late) + '/' + str(mav_r.total):>10}"
              f"{max(mav_r.worst_overdue, 0.0):>13.1f} min")
    print("\n  avg trip      = average drive to the patient; it depends on which ambulance")
    print("                  is sent, not on the order of the queue")
    print("  worst overdue = minutes past its own target for the worst-served call\n")


def main() -> int:
    mav_parser = argparse.ArgumentParser(description=__doc__)
    mav_parser.add_argument("--calls", type=int, default=100)
    mav_parser.add_argument("--minutes", type=float, default=60.0,
                            help="simulated minutes over which the calls arrive")
    mav_parser.add_argument("--scale", type=float, default=300.0,
                            help="how many times faster than real time to run")
    mav_parser.add_argument("--schedulers", nargs="+", default=list(SCHEDULERS),
                            choices=SCHEDULERS)
    mav_parser.add_argument("--port", type=int, default=8023)
    mav_args = mav_parser.parse_args()

    mav_stream = build_calls(mav_args.calls, mav_args.minutes)
    print(f"\n{mav_args.calls} calls, mix: " + ", ".join(
        f"{sum(1 for _, mav_b in mav_stream if mav_b['severity'] == mav_s)} {mav_s}"
        for mav_s in SEVERITIES) + "\n")

    mav_results = [
        run(mav_s, mav_stream, mav_args.scale, mav_args.port)
        for mav_s in mav_args.schedulers
    ]
    print_report(mav_results, mav_args.calls, mav_args.minutes, mav_args.scale)
    reset_database()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
