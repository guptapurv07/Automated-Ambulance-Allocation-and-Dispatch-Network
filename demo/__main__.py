"""Terminal demonstration of the dispatch system.

    python -m demo               # menu
    python -m demo --scene 2     # run one scene and exit

The program is a caller: it talks to the server over the same HTTP API as any
other client and prints what the system does. It holds no logic of its own and
never touches the database, so everything on the screen is the real system at
work: intake, duplicate check, queue, row locking and the ambulance's trip.

If no server is running it starts one and stops it again on exit. To watch the
server's own log beside the demonstration, start the server yourself in a
second terminal first. `--no-access-log` leaves out the line the server prints
for every request, so that the dispatch steps are easy to read:

    uvicorn app.main:app --no-access-log
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx

from app.geo import haversine_km

DEFAULT_URL = "http://127.0.0.1:8000"
SERVER_LOG = Path(__file__).with_name("server.log")

# Named places in and around Dehradun for the caller to choose from, the city
# first and then the outer areas. Positions are approximate, like the stations
# in sim/seed.py.
PLACES: list[tuple[str, float, float]] = [
    ("Paltan Bazaar", 30.3220, 78.0390),
    ("Railway Station", 30.3140, 78.0330),
    ("Prince Chowk", 30.3150, 78.0370),
    ("Astley Hall", 30.3290, 78.0470),
    ("Survey Chowk", 30.3270, 78.0560),
    ("Kishan Nagar Chowk", 30.3290, 78.0240),
    ("Bindal Bridge", 30.3240, 78.0300),
    ("Ballupur Chowk", 30.3330, 78.0110),
    ("Aarghar Chowk", 30.3060, 78.0470),
    ("Rispana Bridge", 30.2960, 78.0520),
    ("ISBT Flyover", 30.2870, 77.9990),
    ("Haridwar Bypass", 30.2740, 78.0480),
    ("Clement Town Market", 30.2660, 78.0100),
    ("Graphic Era University", 30.2686, 77.9950),
    ("Forest Research Institute", 30.3420, 77.9990),
    ("Tapkeshwar Temple", 30.3450, 78.0190),
    ("Pacific Mall, Rajpur Road", 30.3540, 78.0610),
    ("Mussoorie Diversion", 30.3760, 78.0760),
    ("Robber's Cave", 30.3790, 78.0570),
    ("Malsi Deer Park", 30.3850, 78.0780),
    ("IT Park, Sahastradhara Road", 30.3560, 78.0790),
    ("Sahastradhara Road", 30.3500, 78.0900),
    ("Prem Nagar Market", 30.3340, 77.9590),
    ("Selaqui Industrial Area", 30.3720, 77.8650),
    ("Vikasnagar Market", 30.4700, 77.7750),
    ("Mussoorie Mall Road", 30.4570, 78.0770),
    ("Kempty Falls", 30.4880, 77.9980),
    ("Lachhiwala", 30.2150, 78.1040),
    ("Doiwala", 30.1790, 78.1230),
    ("Jolly Grant Airport", 30.1880, 78.1830),
]


def place(name: str) -> tuple[str, float, float]:
    """The entry in PLACES with this name."""
    return next(mav_p for mav_p in PLACES if mav_p[0] == name)


SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
TYPES: list[tuple[str, str]] = [
    ("ROAD_ACCIDENT", "Road accident"),
    ("CARDIAC", "Heart attack"),
    ("BREATHING", "Breathing problem"),
    ("INJURY", "Injury"),
    ("FIRE", "Fire"),
    ("PREGNANCY", "Pregnancy"),
    ("OTHER", "Other"),
]
TYPE_NAMES = dict(TYPES)

# Scene 4, part 1: three calls close together in each of ten places spread over
# the district, so the three at one place all have the same nearest ambulance.
# The three differ in type, so the duplicate check does not merge them: these
# are thirty separate emergencies, one for each ambulance in the fleet.
BURST_PLACES: list[tuple[str, float, float]] = [
    ("Clock Tower", 30.3255, 78.0413),
    ("ISBT", 30.2892, 77.9959),
    ("Rajpur Road", 30.3563, 78.0664),
    ("Prem Nagar", 30.3320, 77.9412),
    ("Raipur", 30.3241, 78.1086),
    ("Clement Town", 30.2666, 78.0064),
    ("Selaqui", 30.3696, 77.8558),
    ("Doiwala", 30.1786, 78.1203),
    ("Mussoorie", 30.4598, 78.0644),
    ("Vikasnagar", 30.4686, 77.7736),
]
# (severity, type), taken three at a time for each place in turn.
BURST_MIX: list[tuple[str, str]] = [
    ("CRITICAL", "CARDIAC"), ("MEDIUM", "INJURY"), ("LOW", "OTHER"),
    ("HIGH", "ROAD_ACCIDENT"), ("LOW", "INJURY"), ("CRITICAL", "BREATHING"),
    ("MEDIUM", "PREGNANCY"), ("HIGH", "FIRE"), ("LOW", "OTHER"),
    ("CRITICAL", "ROAD_ACCIDENT"), ("HIGH", "CARDIAC"), ("MEDIUM", "BREATHING"),
]
# Where the three callers at one place stand, in degrees from it (about 100 m).
BURST_OFFSETS: list[tuple[float, float]] = [(0.0007, 0.0007), (-0.0007, -0.0005), (0.0005, -0.0008)]
# (place, severity, type, latitude, longitude)
BURST_CALLS: list[tuple[str, str, str, float, float]] = [
    (mav_name, *BURST_MIX[(3 * mav_k + mav_j) % len(BURST_MIX)], mav_lat + mav_d[0], mav_lon + mav_d[1])
    for mav_k, (mav_name, mav_lat, mav_lon) in enumerate(BURST_PLACES)
    for mav_j, mav_d in enumerate(BURST_OFFSETS)
]
# Scene 4, part 2: (severity, type, latitude, longitude). Five calls that arrive
# while the fleet is busy, the least serious first.
LATE_CALLS: list[tuple[str, str, float, float]] = [
    ("LOW", "INJURY", 30.3180, 78.0300),
    ("LOW", "OTHER", 30.3195, 78.0420),
    ("MEDIUM", "OTHER", 30.3215, 78.0345),
    ("HIGH", "BREATHING", 30.3150, 78.0380),
    ("CRITICAL", "CARDIAC", 30.3240, 78.0290),
]

# The ambulance's phases in order, with what the screen says for each.
STEPS: list[tuple[str, str]] = [
    ("ASSIGNED", "Ambulance allocated"),
    ("EN_ROUTE", "Dispatched"),
    ("AT_SCENE", "Reached the patient"),
    ("TRANSPORTING", "Taking the patient"),
    ("AT_HOSPITAL", "Reached the hospital"),
    ("AVAILABLE", "Ambulance free again"),
]
PHASES = [mav_step[0] for mav_step in STEPS]

SCHEDULER_NAMES = {
    "fifo": "FIFO (first come, first served)",
    "priority": "Priority (most severe first)",
    "llf": "Least Laxity First (least spare time first)",
}

# How long a scene keeps watching before it gives up, in real seconds.
FOLLOW_SECONDS = 240.0
POLL_SECONDS = 0.25

USE_COLOUR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
COLOURS = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}


def paint(text: str, colour: str) -> str:
    if not USE_COLOUR:
        return text
    return f"\033[{COLOURS[colour]}m{text}\033[0m"


def heading(text: str) -> None:
    print()
    print(paint("=" * 78, "dim"))
    print(paint(f"  {text}", "bold"))
    print(paint("=" * 78, "dim"))


def note(text: str) -> None:
    print(paint(f"  {text}", "dim"))


def count(number: int, word: str) -> str:
    """'1 ambulance', '3 ambulances'."""
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def listed(words: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


class Demo:
    """One connection to the server, and the scenes that use it."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.client = httpx.Client(base_url=self.url, timeout=30.0)
        self.server: subprocess.Popen | None = None
        self.time_scale = 60.0

    # --- the server ---------------------------------------------------------

    def health(self) -> dict | None:
        try:
            mav_reply = self.client.get("/health", timeout=2.0)
        except httpx.HTTPError:
            return None
        return mav_reply.json() if mav_reply.status_code == 200 else None

    def connect(self) -> bool:
        """Find the server, or start one. Returns False if neither works."""
        if self.health() is None:
            mav_port = self.url.rsplit(":", 1)[-1]
            print(f"\n  No server at {self.url}. Starting one "
                  f"(its log goes to {os.path.relpath(SERVER_LOG)}) ...")
            mav_log = open(SERVER_LOG, "w")
            self.server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--port", mav_port,
                 "--no-access-log"],
                stdout=mav_log,
                stderr=subprocess.STDOUT,
            )
            for _ in range(60):
                if self.server.poll() is not None or self.health() is not None:
                    break
                time.sleep(0.25)

        mav_health = self.health()
        if mav_health is None:
            print(paint("\n  The server could not be started.", "red"))
            print(f"  Look at {SERVER_LOG} for the reason, or start it yourself with:")
            print("      uvicorn app.main:app --no-access-log\n")
            return False
        if mav_health["database"] != "connected":
            print(paint("\n  The server is running but cannot reach MySQL.", "red"))
            print("  Check that MySQL is running and that .env has the password, then")
            print("  run:  python -m sim.seed\n")
            return False
        # No incident has id 0, so the answer should be "not found". An error
        # here means the tables were made by an older version of the code.
        if self.client.get("/incidents/0").status_code >= 500:
            print(paint("\n  The database tables are older than the code.", "red"))
            print("  Stop the server, then run:  python -m sim.seed --reset\n")
            return False
        return True

    def close(self) -> None:
        self.client.close()
        if self.server is not None and self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.server.kill()
            print("  The server started by the demonstration has been stopped.")

    def show_title(self) -> None:
        """Print the title, and read the clock speed the time stamps are worked out from."""
        mav_clock = self.client.get("/queue").json()["clock"]
        self.time_scale = float(mav_clock.get("time_scale") or 60.0)

        heading("AUTOMATED AMBULANCE ALLOCATION AND DISPATCH NETWORK")
        if not mav_clock.get("running"):
            print(paint("  The simulation clock is off: ambulances will not move by themselves.", "yellow"))

    # --- small helpers ------------------------------------------------------

    def stamp(self, started: float) -> str:
        """Simulated time since `started`, as minutes:seconds."""
        mav_seconds = int((time.monotonic() - started) * self.time_scale)
        return f"{mav_seconds // 60:02d}:{mav_seconds % 60:02d}"

    def event(self, started: float, what: str, detail: str, colour: str = "cyan") -> None:
        print(f"  {paint(self.stamp(started), 'dim')}  {paint(f'{what:<22}', colour)}{detail}")

    def report(self, lat: float, lon: float, **fields) -> httpx.Response:
        return self.client.post("/incidents", json={"lat": lat, "lon": lon, **fields})

    def fleet(self) -> list[dict]:
        return self.client.get("/ambulances").json()

    def free_count(self) -> int:
        return sum(1 for mav_amb in self.fleet() if mav_amb["status"] == "AVAILABLE")

    def units(self, incident_id: int) -> list[dict]:
        """Every patient of an incident with its ambulance; one entry if there is one patient."""
        mav_data = self.client.get(f"/incidents/{incident_id}").json()
        if mav_data["units"]:
            return mav_data["units"]
        return [{
            "incident_id": incident_id,
            "status": mav_data["incident"]["status"],
            "dispatch": mav_data["dispatch"],
            "ambulance": mav_data["ambulance"],
            "hospital": mav_data["hospital"],
        }]

    def wait_for_fleet(self, needed: int) -> None:
        """Let earlier scenes finish, so this one starts with enough ambulances."""
        mav_free = self.free_count()
        if mav_free >= needed:
            return
        note(f"{mav_free} ambulances are free; waiting for {needed} "
             "(earlier calls are still finishing) ...")
        mav_until = time.monotonic() + 90.0
        while time.monotonic() < mav_until and self.free_count() < needed:
            time.sleep(0.5)

    # --- following ambulances -----------------------------------------------

    def follow(
        self,
        incident_id: int,
        started: float,
        until: str = "AVAILABLE",
        seen: dict | None = None,
        seconds: float = FOLLOW_SECONDS,
    ) -> list[dict]:
        """Print every step of every ambulance of an incident, as it happens.

        Stops when each patient's ambulance has reached `until`, or after
        `seconds`. Returns the latest state of each patient. Pass the same
        `seen` to a later call to carry on without printing a step twice.
        """
        mav_goal = PHASES.index(until)
        mav_seen = seen if seen is not None else {}
        mav_next: dict[int, int] = mav_seen.setdefault("next", {})    # per patient: next step to print
        mav_waiting: set[int] = mav_seen.setdefault("waiting", set())  # patients already shown as waiting
        mav_deadline = time.monotonic() + seconds
        mav_units: list[dict] = []

        while time.monotonic() < mav_deadline:
            mav_units = self.units(incident_id)
            mav_many = len(mav_units) > 1
            for mav_number, mav_unit in enumerate(mav_units, start=1):
                mav_id = mav_unit["incident_id"]
                mav_who = f"Patient {mav_number}: " if mav_many else ""
                mav_dispatch, mav_amb = mav_unit["dispatch"], mav_unit["ambulance"]

                if mav_dispatch is None:
                    if mav_unit["status"] == "UNASSIGNED" and mav_id not in mav_waiting:
                        mav_waiting.add(mav_id)
                        self.event(started, "Waiting", f"{mav_who}no ambulance is free yet", "yellow")
                    continue

                # A finished dispatch is "free again" whatever the ambulance is
                # doing now; it may already be on its next call.
                if mav_dispatch["status"] == "COMPLETED":
                    mav_reached = len(PHASES) - 1
                elif mav_amb["status"] in PHASES:
                    mav_reached = PHASES.index(mav_amb["status"])
                else:
                    continue
                # Print every step up to the one reached, so a step that came
                # and went between two looks is still shown.
                mav_upto = min(mav_reached, mav_goal)
                for mav_step in range(mav_next.get(mav_id, 0), mav_upto + 1):
                    self.event(started, STEPS[mav_step][1], mav_who + self.describe(mav_step, mav_unit),
                               "green" if mav_step == len(STEPS) - 1 else "cyan")
                mav_next[mav_id] = max(mav_next.get(mav_id, 0), mav_upto + 1)

            if mav_units and all(mav_next.get(mav_unit["incident_id"], 0) > mav_goal for mav_unit in mav_units):
                return mav_units
            time.sleep(POLL_SECONDS)

        if until == "AVAILABLE":
            note("Stopped watching: this is taking longer than expected.")
        return mav_units

    @staticmethod
    def describe(step: int, unit: dict) -> str:
        mav_amb, mav_dispatch, mav_hospital = unit["ambulance"], unit["dispatch"], unit["hospital"]
        mav_sign = mav_amb["call_sign"]
        mav_where = mav_hospital["name"] if mav_hospital else "the nearest hospital"
        return [
            f"{mav_sign} from {mav_amb['station_name']} station, "
            f"{mav_dispatch['distance_km']:.1f} km away, about {mav_dispatch['eta_minutes']:.1f} minutes",
            f"{mav_sign} is on its way",
            f"{mav_sign} is at the scene",
            f"{mav_sign} is going to {mav_where}",
            f"{mav_sign} is at {mav_where}",
            f"{mav_sign} is available",
        ][step]

    # --- scene 1 ------------------------------------------------------------

    def scene_one_call(self, scripted: bool = False) -> None:
        heading("SCENE 1   One emergency, from the call to the hospital")
        if scripted:
            mav_place, mav_lat, mav_lon = place("Railway Station")
            mav_severity, mav_type, mav_patients = "CRITICAL", "CARDIAC", 1
        else:
            mav_place, mav_lat, mav_lon = ask_place()
            mav_severity = SEVERITIES[ask_choice("Severity", list(SEVERITIES), default=1) - 1]
            mav_type = TYPES[ask_choice("Type of emergency", [mav_t[1] for mav_t in TYPES], default=1) - 1][0]
            mav_patients = ask_number("How many people are injured", default=1, low=1, high=10)

        print()
        mav_started = time.monotonic()
        mav_reply = self.report(mav_lat, mav_lon, severity=mav_severity, incident_type=mav_type,
                                patients=mav_patients, description=f"{TYPE_NAMES[mav_type]} at {mav_place}")
        if mav_reply.status_code == 422:
            self.event(mav_started, "Call refused", refusal(mav_reply), "red")
            return
        mav_body = mav_reply.json()
        mav_id = mav_body["incident_id"]

        if mav_body["duplicate"]:
            self.event(mav_started, "Call received", f"{mav_severity}, {TYPE_NAMES[mav_type].lower()} at {mav_place}")
            self.event(mav_started, "Already reported", f"this is the same emergency as incident {mav_id} "
                       f"({mav_body['report_count']} reports so far); no second ambulance is sent", "yellow")
            note("Following the ambulance that is already handling it.")
        else:
            mav_people = "" if mav_patients == 1 else f", {mav_patients} patients"
            self.event(mav_started, "Call received", f"Incident {mav_id}, {mav_severity}, "
                       f"{TYPE_NAMES[mav_type].lower()} at {mav_place}{mav_people}")
        self.follow(mav_id, mav_started)

    # --- scene 2 ------------------------------------------------------------

    def scene_many_callers(self) -> None:
        heading("SCENE 2   Five people report the same accident")
        mav_place, mav_lat, mav_lon = place("Haridwar Bypass")
        # Five bystanders standing a few metres apart, calling at the same instant.
        mav_callers = [(mav_lat + mav_k * 0.00004, mav_lon + mav_k * 0.00003, f"98970000{mav_k:02d}")
                       for mav_k in range(5)]
        mav_spread = max(haversine_km(mav_lat, mav_lon, mav_c[0], mav_c[1]) for mav_c in mav_callers) * 1000
        print(f"  A road accident at {mav_place}. Five callers, up to {mav_spread:.0f} m apart, "
              "call at the same instant.\n")

        mav_started = time.monotonic()
        with ThreadPoolExecutor(max_workers=5) as mav_pool:
            mav_futures = [
                mav_pool.submit(self.report, mav_c[0], mav_c[1], severity="HIGH", incident_type="ROAD_ACCIDENT",
                                caller_phone=mav_c[2], description=f"Accident at {mav_place}")
                for mav_c in mav_callers
            ]
            mav_replies = [mav_f.result().json() for mav_f in mav_futures]
        mav_took_ms = (time.monotonic() - mav_started) * 1000

        mav_id = mav_replies[0]["incident_id"]
        for mav_number, mav_body in enumerate(mav_replies, start=1):
            if mav_body["duplicate"]:
                print(f"  Caller {mav_number}  ->  {paint('same emergency', 'yellow')} as incident "
                      f"{mav_body['incident_id']}  ({mav_body['report_count']} reports so far)")
            else:
                print(f"  Caller {mav_number}  ->  {paint('new incident', 'green')} {mav_body['incident_id']}")
        note("The report numbers show the order in which the five calls got through the lock.")

        print()
        self.follow(mav_id, mav_started, until="ASSIGNED")
        mav_incidents = sorted({mav_body["incident_id"] for mav_body in mav_replies})
        mav_reports = self.client.get(f"/incidents/{mav_id}").json()["incident"]["report_count"]
        mav_sent = sum(1 for mav_one in mav_incidents if self.units(mav_one)[0]["dispatch"])

        print(f"\n  {paint('Result:', 'bold')} 5 calls in {mav_took_ms:.0f} ms  ->  "
              f"{count(len(mav_incidents), 'incident')}, {count(mav_reports, 'report')} on it, "
              f"{count(mav_sent, 'ambulance')} sent.")
        if len(mav_incidents) > 1:
            print(paint("  The calls were NOT merged. Is the duplicate check switched off "
                        "(DEDUP_ENABLED=false)?", "red"))
            return
        print("\n  Why they were treated as one emergency:")
        print("    Type         all five said ROAD_ACCIDENT")
        print(f"    Place        the callers were {mav_spread:.0f} m apart (same 100 m square of the city, or the next one)")
        print("    Time         all within the same 2-minute window")
        print("    Still open   the ambulance has not left the scene with the patient")
        note("Two guards make this safe when the calls arrive together: a mutex around the check,")
        note("and a UNIQUE key in MySQL that refuses a second incident for the same emergency.")

    # --- scene 3 ------------------------------------------------------------

    def scene_many_patients(self) -> None:
        heading("SCENE 3   One accident, several critically injured, three callers")
        mav_place, mav_lat, mav_lon = place("Forest Research Institute")
        self.wait_for_fleet(5)
        print(f"  A bus accident near {mav_place}. Three people call, and they do not agree on\n"
              "  how many are injured.\n")

        mav_started = time.monotonic()
        mav_calls = [
            (0.0, 0.0, 4, "4 people badly hurt"),
            (0.00004, 0.00002, 5, "5 injured, one is trapped"),
            (-0.00003, 0.00003, 5, "bus accident, 5 injured"),
        ]
        mav_id = None
        mav_seen: dict = {}
        for mav_number, (mav_d_lat, mav_d_lon, mav_patients, mav_words) in enumerate(mav_calls, start=1):
            mav_phone = f"98971111{mav_number:02d}"
            mav_at = (mav_lat + mav_d_lat, mav_lon + mav_d_lon)
            # What this caller told the system, exactly as it is sent.
            mav_apart = haversine_km(mav_lat, mav_lon, mav_at[0], mav_at[1]) * 1000
            print(f"  {paint(f'Caller {mav_number}', 'bold')}   phone {mav_phone}")
            print(f"      where     {mav_place} ({mav_at[0]:.5f}, {mav_at[1]:.5f})"
                  + ("" if mav_number == 1 else f", {mav_apart:.0f} m from caller 1"))
            print(f"      reports   {TYPE_NAMES['ROAD_ACCIDENT']}, CRITICAL, {mav_patients} injured")
            print(f"      says      \"{mav_words}\"")
            mav_body = self.report(mav_at[0], mav_at[1], severity="CRITICAL",
                                   incident_type="ROAD_ACCIDENT", patients=mav_patients,
                                   caller_phone=mav_phone, description=mav_words).json()
            mav_id = mav_body["incident_id"]
            mav_asked = len(mav_body["incident_ids"])
            if not mav_body["duplicate"]:
                mav_result = f"{paint('new incident', 'green')} {mav_id}, {count(mav_asked, 'ambulance')} requested"
            elif mav_asked:
                mav_result = (f"{paint('same emergency', 'yellow')}, but more patients: "
                              f"{count(mav_asked, 'more ambulance')} requested")
            else:
                mav_result = f"{paint('same emergency', 'yellow')}, nothing more needed"
            print(f"      system    {mav_result}")
            # Show the ambulances this caller's report set in motion.
            self.follow(mav_id, mav_started, until="ASSIGNED", seen=mav_seen, seconds=3.0)
            print()

        # What the three reports have become in the database: one incident.
        mav_record = self.client.get(f"/incidents/{mav_id}").json()["incident"]
        print(f"  {paint('On record:', 'bold')} incident {mav_id}, {mav_record['severity']}, "
              f"{count(mav_record['patients'], 'patient')}, {count(mav_record['report_count'], 'report')}")
        print(f"      what the callers said   \"{mav_record['description']}\"\n")

        mav_units = self.follow(mav_id, mav_started, seen=mav_seen)
        mav_signs = [mav_unit["ambulance"]["call_sign"] for mav_unit in mav_units if mav_unit["ambulance"]]
        mav_hospitals = sorted({mav_unit["hospital"]["name"] for mav_unit in mav_units if mav_unit["hospital"]})
        print(f"\n  {paint('Result:', 'bold')} 3 callers, 5 patients  ->  {count(len(mav_signs), 'ambulance')} "
              f"({', '.join(mav_signs)}), {len(set(mav_signs))} different.")
        print(f"  Not 3 (one per caller), and not 14 (4 + 5 + 5). Taken to: {'; '.join(mav_hospitals)}.")
        note("Each patient is a separate request in the queue, so the row lock gives each one a")
        note("different ambulance. The number of patients is raised by a later caller, never lowered.")

    # --- scene 4 ------------------------------------------------------------

    def scene_many_emergencies(self) -> None:
        mav_fleet = len(self.fleet())
        heading(f"SCENE 4   {len(BURST_CALLS) + len(LATE_CALLS)} emergencies, {mav_fleet} ambulances")
        self.wait_for_fleet(mav_fleet)
        mav_started = time.monotonic()
        mav_running = self.part_same_instant(mav_started)
        if mav_running is None:
            note("Part 2 is left out: with an ambulance on two calls the data is no longer valid.")
            note("Stop the server and run:  python -m sim.seed --reset")
            return
        self.part_while_busy(mav_started, mav_running)

    def part_same_instant(self, started: float) -> list[int] | None:
        """Part 1: one call for every free ambulance, all at once, competing for the
        same nearest ambulances.

        Returns the dispatches now running, or None if an ambulance was given
        to two calls.
        """
        mav_free = self.free_count()
        mav_calls = BURST_CALLS[:mav_free]
        mav_places = list(dict.fromkeys(mav_call[0] for mav_call in mav_calls))
        print(f"  Part 1. {len(mav_calls)} calls arrive at the same instant, three near each of "
              f"{len(mav_places)} places.\n"
              f"  The three calls at one place all have the same nearest ambulance. "
              f"{count(mav_free, 'ambulance')} free.\n")

        with ThreadPoolExecutor(max_workers=len(mav_calls)) as mav_pool:
            mav_futures = [mav_pool.submit(self.report, mav_lat, mav_lon, severity=mav_sev, incident_type=mav_type)
                           for _, mav_sev, mav_type, mav_lat, mav_lon in mav_calls]
            mav_ids = [mav_f.result().json()["incident_id"] for mav_f in mav_futures]

        # A call whose nearest ambulances were all taken by other calls a moment
        # earlier is tried again on the next clock tick, so allow a few seconds.
        mav_rows: list[dict] = []
        mav_until = time.monotonic() + 15.0
        while time.monotonic() < mav_until:
            mav_rows = [self.client.get(f"/incidents/{mav_id}").json() for mav_id in mav_ids]
            if sum(1 for mav_row in mav_rows if mav_row["dispatch"]) >= len(mav_calls):
                break
            time.sleep(0.2)

        # One line per place: which ambulance each of its three calls got.
        print(f"  {'place':<14}{'ambulances given to its three calls, with the distance'}")
        print(paint("  " + "-" * 64, "dim"))
        for mav_name in mav_places:
            mav_given = []
            for mav_row, mav_call in zip(mav_rows, mav_calls):
                if mav_call[0] != mav_name:
                    continue
                if mav_row["dispatch"]:
                    mav_given.append(f"{mav_row['ambulance']['call_sign']} {mav_row['dispatch']['distance_km']:4.1f} km")
                else:
                    mav_given.append(paint(f"{'waiting':<14}", "yellow"))
            print(f"  {mav_name:<14}" + "   ".join(f"{mav_g:<14}" for mav_g in mav_given))

        mav_used = [mav_row["ambulance"]["call_sign"] for mav_row in mav_rows if mav_row["dispatch"]]
        mav_twice = sorted({mav_sign for mav_sign in mav_used if mav_used.count(mav_sign) > 1})
        print(f"\n  {paint('Result:', 'bold')} {count(len(mav_used), 'call')} got an ambulance, "
              f"{len(set(mav_used))} different ambulances.")
        if mav_twice:
            print(paint(f"  DOUBLE-BOOKED: {listed(mav_twice)} went to more than one emergency. "
                        "Is row locking switched off?", "red"))
            return None
        print(paint("  No ambulance was given to two calls.", "green"))
        note("At each place one call got the nearest ambulance and the others got the next nearest.")
        note("Every worker thread locks the ambulance rows before it assigns one, so two workers")
        note("can never take the same ambulance.")
        return [mav_row["dispatch"]["id"] for mav_row in mav_rows if mav_row["dispatch"]]

    def part_while_busy(self, started: float, running: list[int]) -> None:
        """Part 2: five calls that have to wait, and the order they are served in.

        `running` are the dispatches of part 1. So that nobody has to sit
        through whole trips, they are finished early, one at a time, through
        the manual endpoint POST /dispatches/{id}/complete. Which waiting call
        gets each freed ambulance is still the scheduler's own decision.
        """
        mav_free = self.free_count()
        print(f"\n  Part 2. {count(mav_free, 'ambulance')} free now. {len(LATE_CALLS)} more calls arrive, "
              "the least serious first:\n")
        mav_waiting: dict[int, tuple[int, str]] = {}   # incident id -> (call number, severity)
        for mav_number, (mav_sev, mav_type, mav_lat, mav_lon) in enumerate(LATE_CALLS, start=len(running) + 1):
            mav_body = self.report(mav_lat, mav_lon, severity=mav_sev, incident_type=mav_type).json()
            mav_waiting[mav_body["incident_id"]] = (mav_number, mav_sev)
            self.event(started, f"Call {mav_number} ({mav_sev})",
                       f"{TYPE_NAMES[mav_type]}, accepted" + ("" if mav_free else " and waiting"), "yellow")
            time.sleep(0.2)

        if not mav_free:
            print("\n  To save time, the running calls are now finished early, one at a time.\n"
                  "  Each time one ambulance becomes free. Which waiting call gets it?\n")
        mav_running = list(running)
        mav_served: list[tuple[datetime, str, str]] = []   # (when, severity, ambulance)
        mav_deadline = time.monotonic() + FOLLOW_SECONDS
        while mav_waiting and time.monotonic() < mav_deadline:
            # Free one ambulance, unless one is free already.
            while mav_running and self.free_count() == 0:
                mav_reply = self.client.post(f"/dispatches/{mav_running.pop(0)}/complete")
                if mav_reply.status_code == 200:   # 409: that call had already finished by itself
                    self.event(started, f"{mav_reply.json()['call_sign']} is free", "its call has been finished")
                    break

            # Wait for the scheduler to hand that ambulance to a waiting call.
            mav_new: list[tuple[datetime, int, str, dict]] = []
            mav_until = time.monotonic() + 5.0
            while not mav_new and time.monotonic() < mav_until:
                for mav_id, (mav_number, mav_sev) in list(mav_waiting.items()):
                    mav_row = self.client.get(f"/incidents/{mav_id}").json()
                    if mav_row["dispatch"]:
                        del mav_waiting[mav_id]
                        mav_when = datetime.fromisoformat(mav_row["dispatch"]["assigned_at"])
                        mav_new.append((mav_when, mav_number, mav_sev, mav_row))
                if not mav_new:
                    time.sleep(0.1)
            # In the order the server assigned them, not the order they were looked up.
            for mav_when, mav_number, mav_sev, mav_row in sorted(mav_new, key=lambda mav_item: mav_item[0]):
                mav_served.append((mav_when, mav_sev, mav_row["ambulance"]["call_sign"]))
                self.event(started, f"Call {mav_number} ({mav_sev})",
                           f"gets {mav_row['ambulance']['call_sign']}, "
                           f"{mav_row['dispatch']['distance_km']:.1f} km away", "green")
        mav_served.sort(key=lambda mav_item: mav_item[0])

        # One ambulance cannot serve two waiting calls at once.
        mav_signs = [mav_item[2] for mav_item in mav_served]
        mav_twice = sorted({mav_sign for mav_sign in mav_signs if mav_signs.count(mav_sign) > 1})
        if mav_twice:
            print(paint(f"\n  DOUBLE-BOOKED: {listed(mav_twice)} went to more than one emergency at "
                        "the same time. Is row locking switched off?", "red"))

        # Calls that got their ambulances in the same instant were served
        # together: more than one ambulance was free on that tick of the clock.
        mav_levels: list[list[str]] = []
        mav_last: datetime | None = None
        for mav_when, mav_sev, _ in mav_served:
            if mav_last is not None and (mav_when - mav_last).total_seconds() < 0.3:
                mav_levels[-1].append(mav_sev)
            else:
                mav_levels.append([mav_sev])
            mav_last = mav_when
        # "CRITICAL, HIGH and 2 MEDIUM together"
        mav_parts = [
            listed([mav_sev if mav_level.count(mav_sev) == 1 else f"{mav_level.count(mav_sev)} {mav_sev}"
                    for mav_sev in SEVERITIES if mav_sev in mav_level])
            + (" together" if len(mav_level) > 1 else "")
            for mav_level in mav_levels
        ]

        mav_scheduler = (self.health() or {}).get("queue_scheduler", "")
        print(f"\n  {paint('Result:', 'bold')} served in this order: {', then '.join(mav_parts)}.")
        if len(mav_levels) > 1 and mav_levels[0] == ["CRITICAL"]:
            print("  The CRITICAL call arrived last and got the first free ambulance.")
        if len(mav_levels) > 1 and set(mav_levels[-1]) == {"LOW"}:
            print("  The LOW calls arrived first, and a LOW call was the last to be served.")
        note(f"The order is decided by the scheduler: {SCHEDULER_NAMES.get(mav_scheduler, mav_scheduler)}.")
        note("A call that has to wait is never dropped.")

    # --- scene 5 ------------------------------------------------------------

    def scene_outside_city(self) -> None:
        heading("SCENE 5   A call from outside the city")
        print("  A call arrives with a position in Delhi (28.6139, 77.2090).\n")
        mav_started = time.monotonic()
        mav_free = self.free_count()
        mav_reply = self.report(28.6139, 77.2090, severity="CRITICAL", incident_type="ROAD_ACCIDENT")
        if mav_reply.status_code == 422:
            self.event(mav_started, "Call refused", refusal(mav_reply), "red")
            print(f"\n  {paint('Result:', 'bold')} nothing was recorded and no ambulance was sent "
                  f"({mav_free} free before the call, {self.free_count()} after).")
            note("Without this check a wrong position sent an ambulance on a 195 km trip.")
        else:
            print(paint(f"  The call was accepted (HTTP {mav_reply.status_code}); {mav_free} were free.", "yellow"))

    # --- scene 6 ------------------------------------------------------------

    def scene_fleet(self) -> None:
        heading("SCENE 6   The fleet and the queue right now")
        mav_fleet = self.fleet()
        print(f"  {'ambulance':<11}{'station':<20}{'status':<14}")
        print(paint("  " + "-" * 43, "dim"))
        for mav_amb in mav_fleet:
            mav_colour = "green" if mav_amb["status"] == "AVAILABLE" else "yellow"
            print(f"  {mav_amb['call_sign']:<11}{mav_amb['station_name']:<20}"
                  f"{paint(mav_amb['status'], mav_colour)}")
        mav_free = sum(1 for mav_amb in mav_fleet if mav_amb["status"] == "AVAILABLE")
        mav_stats = self.client.get("/queue").json()
        print(f"\n  {mav_free} of {len(mav_fleet)} ambulances are free.")
        print(f"  Queue    {mav_stats['queue']['depth']} calls waiting for a worker now, "
              f"{mav_stats['queue']['enqueued_total']} queued in total "
              f"(longest queue so far: {mav_stats['queue']['peak_depth']})")
        print(f"  Workers  {mav_stats['workers']['size']} threads, {mav_stats['workers']['handled']} "
              f"requests handled, {mav_stats['workers']['failed']} failed")
        print(f"  Clock    {mav_stats['clock']['transitions']} ambulance steps made, "
              f"{mav_stats['clock']['requeued']} waiting calls put back on the queue")


def refusal(reply: httpx.Response) -> str:
    """The reason the server gave for refusing a call."""
    mav_detail = reply.json().get("detail")
    return mav_detail if isinstance(mav_detail, str) else "the details of the call are not valid"


# --- asking the person at the keyboard --------------------------------------

def ask(prompt: str) -> str:
    """Read one line. A prompt may start with blank lines of its own."""
    mav_lead = prompt[: len(prompt) - len(prompt.lstrip())]
    return input(mav_lead + paint(f"  {prompt.strip()} ", "bold")).strip()


def ask_number(prompt: str, default: int, low: int, high: int) -> int:
    while True:
        mav_text = ask(f"{prompt} ({low}-{high}) [{default}]:")
        if not mav_text:
            return default
        if mav_text.isdigit() and low <= int(mav_text) <= high:
            return int(mav_text)
        print(f"    Please type a number from {low} to {high}.")


def ask_choice(title: str, options: list[str], default: int) -> int:
    """Show a numbered list and return the number chosen."""
    print(f"\n  {title}:")
    for mav_number, mav_option in enumerate(options, start=1):
        print(f"    {mav_number}. {mav_option}")
    return ask_number("Choose", default=default, low=1, high=len(options))


def ask_float(prompt: str) -> float:
    while True:
        try:
            return float(ask(prompt))
        except ValueError:
            print("    Please type a number, for example 30.3165")


def ask_place() -> tuple[str, float, float]:
    print("\n  Where is the emergency?")
    # Two columns, so that the whole list fits on the screen.
    mav_half = (len(PLACES) + 1) // 2
    for mav_row in range(mav_half):
        mav_cells = [
            f"{mav_i + 1:>4}. {PLACES[mav_i][0]:<30}"
            for mav_i in (mav_row, mav_row + mav_half)
            if mav_i < len(PLACES)
        ]
        print("".join(mav_cells).rstrip())
    print("     0. Type a latitude and longitude")
    mav_pick = ask_number("Choose", default=1, low=0, high=len(PLACES))
    if mav_pick:
        return PLACES[mav_pick - 1]
    mav_lat = ask_float("Latitude  (Dehradun is about 30.32):")
    mav_lon = ask_float("Longitude (Dehradun is about 78.03):")
    return f"({mav_lat:.4f}, {mav_lon:.4f})", mav_lat, mav_lon


MENU: list[tuple[str, str]] = [
    ("One emergency, from the call to the hospital", "scene_one_call"),
    ("Five people report the same accident", "scene_many_callers"),
    ("One accident, several critically injured, three callers", "scene_many_patients"),
    ("More emergencies than ambulances", "scene_many_emergencies"),
    ("A call from outside the city", "scene_outside_city"),
    ("The fleet and the queue right now", "scene_fleet"),
]


def run_scene(demo: Demo, number: int, scripted: bool = False) -> None:
    mav_scene = getattr(demo, MENU[number - 1][1])
    try:
        if number == 1:
            mav_scene(scripted=scripted)
        else:
            mav_scene()
    except KeyboardInterrupt:
        print("\n  Stopped. The system carries on in the background.")
    except httpx.HTTPError as exc:
        print(paint(f"\n  Lost contact with the server: {exc}", "red"))


def main() -> int:
    mav_parser = argparse.ArgumentParser(
        prog="python -m demo", description="Terminal demonstration of the dispatch system."
    )
    mav_parser.add_argument("--url", default=DEFAULT_URL, help="address of the server")
    mav_parser.add_argument("--scene", type=int, choices=range(1, len(MENU) + 1),
                            help="run one scene with fixed details and exit")
    mav_args = mav_parser.parse_args()

    mav_demo = Demo(mav_args.url)
    try:
        if not mav_demo.connect():
            return 1
        mav_demo.show_title()

        if mav_args.scene:
            run_scene(mav_demo, mav_args.scene, scripted=True)
            return 0

        while True:
            print()
            for mav_number, mav_entry in enumerate(MENU, start=1):
                print(f"    {mav_number}. {mav_entry[0]}")
            print("    0. Exit")
            mav_text = ask(f"\n  Scene (0-{len(MENU)}):")
            if mav_text == "0":
                return 0
            if mav_text.isdigit() and 1 <= int(mav_text) <= len(MENU):
                run_scene(mav_demo, int(mav_text))
            else:
                print(f"    Please type a number from 0 to {len(MENU)}.")
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    finally:
        mav_demo.close()


if __name__ == "__main__":
    raise SystemExit(main())
