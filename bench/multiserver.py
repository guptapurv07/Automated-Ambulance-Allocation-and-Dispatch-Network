"""Benchmark: two server processes on one database, without Redis and with it.

One server process keeps its queue, the duplicate check's lock and the clock in
its own memory. This starts two servers side by side on the same database,
sends calls to both at the same instant, and measures what each mechanism does
across the two processes, first without Redis and then with it:

    duplicates   6 accidents, each reported by 6 callers at the same instant,
                 3 to each server. The callers stand on either side of a grid
                 corner, so the two servers' reports have different keys: the
                 UNIQUE key cannot merge them, only the lock can.
    allocation   30 different emergencies at the same instant, 15 to each
                 server, competing for the same nearest ambulances.
    clock        how many servers move the ambulances while calls are running.
    queue        how many calls taken in by one server were handled by the
                 other server's workers.

    python -m bench.multiserver
    python -m bench.multiserver --redis-url redis://127.0.0.1:6379/0

Redis has to be running (redis-server). Allocation is safe in both runs: it is
decided by MySQL's row locks, which hold across processes. The duplicate
check's lock and the clock are what lived in one process's memory.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from sqlalchemy import create_engine, text

from app.config import get_settings
from bench.dedup import SITES
from bench.loadtest import reset_database

PORTS = (8031, 8032)
ACCIDENTS, CALLERS = 6, 6
# Where the callers of one accident stand, in degrees from the grid corner it
# happened on (about 25 m): the first server's callers to the north-east, the
# second's to the south-west, so each server's reports have a key of their own.
OFFSETS = ((0.0002, 0.0002), (-0.0002, -0.0002))
# Ten places over the district with three emergencies of different types at
# each: never merged, and the three at one place want the same ambulance.
PLACES = [
    (30.3255, 78.0413), (30.2892, 77.9959), (30.3563, 78.0664), (30.3320, 77.9412),
    (30.3241, 78.1086), (30.2666, 78.0064), (30.3696, 77.8558), (30.1786, 78.1203),
    (30.4598, 78.0644), (30.4686, 77.7736),
]
TYPES = ("CARDIAC", "ROAD_ACCIDENT", "BREATHING")


class Servers:
    """Two servers on a freshly reset database, stopped again on exit."""

    def __init__(self, redis_url: str, clock: bool) -> None:
        self.redis_url = redis_url
        self.env = {
            **os.environ,
            "REDIS_URL": redis_url,
            "LOCKING_MODE": "for_update",
            "RACE_WINDOW_MS": "0",
            "DEDUP_ENABLED": "true",
            "QUEUE_SCHEDULER": "llf",
            "WORKER_POOL_SIZE": "4",
            "SIM_CLOCK_ENABLED": "true" if clock else "false",
        }
        self.urls = [f"http://127.0.0.1:{mav_port}" for mav_port in PORTS]
        self.logs: list[str] = []

    def __enter__(self) -> Servers:
        # The reset clears this database's keys in Redis too, when REDIS_URL is set.
        os.environ["REDIS_URL"] = self.redis_url
        reset_database()
        self._files = [
            tempfile.NamedTemporaryFile(mode="w+", suffix=f".server{mav_n}.log", delete=False)
            for mav_n in (1, 2)
        ]
        self._procs = [
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(mav_port),
                 "--log-level", "warning"],
                env=self.env, stdout=mav_file, stderr=subprocess.STDOUT, text=True,
            )
            for mav_port, mav_file in zip(PORTS, self._files)
        ]
        for mav_url in self.urls:
            for _ in range(60):
                try:
                    if httpx.get(mav_url + "/health", timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                self.__exit__(None, None, None)
                raise RuntimeError(f"server at {mav_url} did not start; is a port in use, or Redis down?")
        return self

    def __exit__(self, *exc) -> None:
        for mav_proc in self._procs:
            mav_proc.terminate()
        for mav_proc in self._procs:
            try:
                mav_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                mav_proc.kill()
        for mav_file in self._files:
            mav_file.close()
            self.logs.append(open(mav_file.name).read())
            os.unlink(mav_file.name)

    def post(self, server: int, body: dict) -> dict:
        return httpx.post(self.urls[server] + "/incidents", json=body, timeout=60).json()


def scalar(sql: str) -> int:
    mav_engine = create_engine(get_settings().database_url)
    try:
        with mav_engine.connect() as mav_conn:
            return int(mav_conn.execute(text(sql)).scalar_one())
    finally:
        mav_engine.dispose()


def settle(seconds: float = 8.0) -> None:
    """Wait until every incident has been dealt with by a worker, or `seconds`."""
    mav_until = time.time() + seconds
    while time.time() < mav_until and scalar(
        "SELECT COUNT(*) FROM incidents WHERE status = 'REPORTED'"
    ):
        time.sleep(0.2)
    time.sleep(0.5)


def duplicates(redis_url: str) -> dict[str, int]:
    """Each accident's callers at the same instant, half to each server."""
    with Servers(redis_url, clock=False) as mav_servers:
        for _, mav_lat, mav_lon in SITES[:ACCIDENTS]:
            mav_calls = [
                (mav_k % 2, {
                    "lat": mav_lat + OFFSETS[mav_k % 2][0], "lon": mav_lon + OFFSETS[mav_k % 2][1],
                    "severity": "CRITICAL", "incident_type": "ROAD_ACCIDENT",
                })
                for mav_k in range(CALLERS)
            ]
            with ThreadPoolExecutor(CALLERS) as mav_pool:
                list(mav_pool.map(lambda mav_c: mav_servers.post(*mav_c), mav_calls))
            time.sleep(0.3)
        settle()
        return {
            "incidents": scalar("SELECT COUNT(*) FROM incidents"),
            "sent": scalar("SELECT COUNT(*) FROM dispatches"),
        }


def allocation(redis_url: str) -> dict[str, int]:
    """30 different emergencies at the same instant, half to each server."""
    with Servers(redis_url, clock=False) as mav_servers:
        mav_calls = [
            (mav_k % 2, {"lat": mav_lat + 0.0005 * mav_j, "lon": mav_lon, "severity": "HIGH",
                         "incident_type": mav_type})
            for mav_k, ((mav_lat, mav_lon), (mav_j, mav_type)) in enumerate(
                (mav_place, mav_t) for mav_place in PLACES for mav_t in enumerate(TYPES)
            )
        ]
        with ThreadPoolExecutor(len(mav_calls)) as mav_pool:
            list(mav_pool.map(lambda mav_c: mav_servers.post(*mav_c), mav_calls))
        settle()
        return {
            "calls": len(mav_calls),
            "sent": scalar("SELECT COUNT(*) FROM dispatches"),
            "ambulances": scalar("SELECT COUNT(DISTINCT ambulance_id) FROM dispatches"),
            "double_booked": scalar(
                "SELECT COUNT(*) FROM (SELECT ambulance_id FROM dispatches WHERE status = 'ACTIVE'"
                " GROUP BY ambulance_id HAVING COUNT(*) > 1) AS twice"
            ),
        }


def clock_and_queue(redis_url: str, calls: int = 20, seconds: float = 15.0) -> dict[str, int]:
    """Calls to both servers, with the clock on, for `seconds`."""
    with Servers(redis_url, clock=True) as mav_servers:
        mav_sent_to: dict[int, int] = {}
        for mav_k in range(calls):
            mav_lat, mav_lon = PLACES[mav_k % len(PLACES)]
            mav_reply = mav_servers.post(mav_k % 2, {
                "lat": mav_lat, "lon": mav_lon + 0.001 * mav_k, "severity": "MEDIUM",
                "incident_type": TYPES[mav_k % len(TYPES)],
            })
            mav_sent_to[mav_reply["incident_id"]] = mav_k % 2
            time.sleep(0.1)
        time.sleep(seconds)
        mav_clocks = [httpx.get(mav_url + "/queue", timeout=5).json()["clock"] for mav_url in mav_servers.urls]
    # Which server's worker picked up each call, from the two logs.
    mav_handled_by = {
        int(mav_m.group(1)): mav_n
        for mav_n, mav_log in enumerate(mav_servers.logs)
        for mav_m in re.finditer(r"incident=(\d+) picked up", mav_log)
    }
    return {
        "calls": calls,
        "clocks": sum(1 for mav_c in mav_clocks if mav_c["transitions"] > 0),
        "steps": sum(mav_c["transitions"] for mav_c in mav_clocks),
        "refused": sum(len(re.findall(r"skipped: Dispatch \d+ has already moved", mav_log))
                       for mav_log in mav_servers.logs),
        "by_other": sum(1 for mav_id, mav_n in mav_sent_to.items() if mav_handled_by.get(mav_id, mav_n) != mav_n),
    }


def main() -> int:
    mav_parser = argparse.ArgumentParser(description=__doc__)
    mav_parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    mav_args = mav_parser.parse_args()

    try:
        httpx.get(f"http://127.0.0.1:{PORTS[0]}/health", timeout=0.5)
        print(f"Port {PORTS[0]} is in use. Stop the server running there first.")
        return 1
    except httpx.HTTPError:
        pass

    mav_runs = {}
    for mav_label, mav_url in (("without Redis", ""), ("with Redis", mav_args.redis_url)):
        print(f"  {mav_label:<14} ", end="", flush=True)
        mav_started = time.time()
        mav_runs[mav_label] = (duplicates(mav_url), allocation(mav_url), clock_and_queue(mav_url))
        print(f"done  ({time.time() - mav_started:.0f}s)")
    # Leave the database, and its keys in Redis, as the other benchmarks do.
    os.environ["REDIS_URL"] = mav_args.redis_url
    reset_database()

    (mav_d0, mav_a0, mav_c0), (mav_d1, mav_a1, mav_c1) = mav_runs.values()
    mav_rows = [
        (f"duplicates   {ACCIDENTS} accidents, {ACCIDENTS * CALLERS} calls",
         f"{mav_d0['incidents']} incidents", f"{mav_d1['incidents']} incidents"),
        ("             ambulances sent", f"{mav_d0['sent']}", f"{mav_d1['sent']}"),
        (f"allocation   {mav_a0['calls']} calls at once",
         f"{mav_a0['sent']} sent, {mav_a0['double_booked']} double-booked",
         f"{mav_a1['sent']} sent, {mav_a1['double_booked']} double-booked"),
        ("clock        servers moving them",
         f"{mav_c0['clocks']} ({mav_c0['refused']} steps tried twice)",
         f"{mav_c1['clocks']} ({mav_c1['refused']} steps tried twice)"),
        (f"queue        of {mav_c0['calls']} calls, handled by",
         f"{mav_c0['by_other']} by the other server", f"{mav_c1['by_other']} by the other server"),
    ]
    print("\n" + "=" * 90)
    print(f"  Two server processes on one database (ports {PORTS[0]} and {PORTS[1]})")
    print("=" * 90 + "\n")
    print(f"  {'':<39}{'without Redis':<28}{'with Redis'}")
    print("  " + "-" * 90)
    for mav_what, mav_without, mav_with in mav_rows:
        print(f"  {mav_what:<39}{mav_without:<28}{mav_with}")
    print("\n  Allocation is safe either way: MySQL's row locks hold across processes.")
    print("  The duplicate check's lock, the queue and the clock lived in one process's")
    print("  memory; with Redis the two servers share them.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
