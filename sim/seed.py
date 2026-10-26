"""Create the database, build the schema, and load reference data.

Credentials are read from your local .env file -- nothing is hard-coded here.

    python -m sim.seed                          # create + seed (skips if already populated)
    python -m sim.seed --reset                  # drop everything and rebuild
    python -m sim.seed --reset --no-history     # rebuild without the past calls

Besides the fleet and the hospitals it loads a history of finished calls, so
that every table holds data the way it would after a month of service. The
benchmarks rebuild without that history, because they count the rows their own
calls create.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, func, select, text

from app.config import get_settings
from app.core import shared
from app.geo import eta_minutes, haversine_km
from app.models import (
    Ambulance,
    AmbulanceStatus,
    Base,
    Dispatch,
    DispatchStatus,
    Hospital,
    Incident,
    IncidentStatus,
    IncidentType,
    Severity,
)

# Service area: Dehradun district, Uttarakhand. Coordinates are approximate
# positions of real localities, used as ambulance standby points. The first
# twelve are the city; the rest cover the outer areas and the nearby towns.
STATIONS: list[tuple[str, str, float, float]] = [
    ("DDN-01", "Clock Tower",            30.3255, 78.0413),
    ("DDN-02", "ISBT Dehradun",          30.2892, 77.9959),
    ("DDN-03", "Rajpur Road",            30.3563, 78.0664),
    ("DDN-04", "Prem Nagar",             30.3320, 77.9412),
    ("DDN-05", "Sahastradhara",          30.3868, 78.1207),
    ("DDN-06", "Clement Town",           30.2666, 78.0064),
    ("DDN-07", "Patel Nagar",            30.3096, 78.0104),
    ("DDN-08", "Jakhan",                 30.3556, 78.0562),
    ("DDN-09", "Balliwala Chowk",        30.3336, 77.9853),
    ("DDN-10", "Raipur",                 30.3241, 78.1086),
    ("DDN-11", "Selaqui",                30.3696, 77.8558),
    ("DDN-12", "Doiwala",                30.1786, 78.1203),
    ("DDN-13", "Mussoorie",              30.4598, 78.0644),
    ("DDN-14", "Vikasnagar",             30.4686, 77.7736),
    ("DDN-15", "Sahaspur",               30.3910, 77.8030),
    ("DDN-16", "Jolly Grant",            30.1897, 78.1803),
    ("DDN-17", "Harrawala",              30.2380, 78.0880),
    ("DDN-18", "Kaulagarh",              30.3560, 78.0120),
    ("DDN-19", "Dalanwala",              30.3170, 78.0590),
    ("DDN-20", "Nehru Colony",           30.2990, 78.0590),
    ("DDN-21", "Ajabpur",                30.2930, 78.0420),
    ("DDN-22", "Mothrowala",             30.2740, 78.0330),
    ("DDN-23", "Garhi Cantt",            30.3450, 78.0040),
    ("DDN-24", "Majra",                  30.2980, 78.0010),
    ("DDN-25", "Bhauwala",               30.3570, 77.8860),
    ("DDN-26", "Thano",                  30.2700, 78.1640),
    ("DDN-27", "Maldevta",               30.3610, 78.1350),
    ("DDN-28", "Ranipokhari",            30.1290, 78.1720),
    ("DDN-29", "Shimla Bypass",          30.3100, 77.9600),
    ("DDN-30", "Mohkampur",              30.3010, 78.0870),
]

# Hospitals in and around Dehradun that take emergency patients, with
# approximate positions and bed counts.
HOSPITALS: list[tuple[str, float, float, int]] = [
    ("Doon Government Medical College Hospital",    30.3245, 78.0436, 500),
    ("Shri Mahant Indiresh Hospital",               30.3006, 78.0163, 750),
    ("Max Super Speciality Hospital",               30.3629, 78.0708, 200),
    ("Synergy Institute of Medical Sciences",       30.2872, 77.9981, 300),
    ("Himalayan Hospital, Jolly Grant",             30.1935, 78.1650, 1000),
    ("Coronation Hospital",                         30.3170, 78.0565, 150),
    ("Combined Medical Institute",                  30.3110, 78.0450, 100),
    ("Kailash Hospital",                            30.2850, 78.0610, 150),
    ("Velmed Hospital",                             30.2780, 78.0020, 150),
    ("Graphic Era Institute of Medical Sciences",   30.3640, 77.9050, 500),
    ("Combined Government Hospital, Prem Nagar",    30.3335, 77.9560, 50),
    ("Community Health Centre, Doiwala",            30.1775, 78.1180, 30),
    ("Community Health Centre, Vikasnagar",         30.4680, 77.7760, 50),
    ("Government St. Mary's Hospital, Mussoorie",   30.4560, 78.0740, 50),
]

# The past calls loaded with the reference data.
HISTORY_CALLS = 1000
HISTORY_DAYS = 30

# Where calls come from most often: (latitude, longitude, share). The rest are
# spread over the whole district.
HOTSPOTS: list[tuple[float, float, float]] = [
    (30.3240, 78.0400, 0.22),   # Clock Tower and Paltan Bazaar
    (30.2890, 77.9980, 0.14),   # ISBT and the Haridwar Bypass junction
    (30.3500, 78.0600, 0.12),   # Rajpur Road
    (30.3330, 77.9500, 0.08),   # Prem Nagar and Chakrata Road
    (30.2850, 78.0550, 0.08),   # Haridwar Road
    (30.1850, 78.1300, 0.06),   # Doiwala and Jolly Grant
]
SPREAD_DEGREES = 0.012   # about 1.3 km

# Shares of the past calls, and what a caller typically says for each type.
SEVERITY_SHARES = {Severity.CRITICAL: 0.10, Severity.HIGH: 0.25, Severity.MEDIUM: 0.40, Severity.LOW: 0.25}
TYPE_SHARES = {
    IncidentType.ROAD_ACCIDENT: 0.30,
    IncidentType.INJURY: 0.18,
    IncidentType.CARDIAC: 0.15,
    IncidentType.BREATHING: 0.12,
    IncidentType.OTHER: 0.12,
    IncidentType.PREGNANCY: 0.10,
    IncidentType.FIRE: 0.03,
}
DESCRIPTIONS = {
    IncidentType.ROAD_ACCIDENT: ["Two-wheeler hit by a car", "Bus and truck collision", "Car went off the road",
                                 "Pedestrian hit by a scooter"],
    IncidentType.INJURY: ["Fell from a height", "Deep cut at a construction site", "Fall at home, cannot stand"],
    IncidentType.CARDIAC: ["Severe chest pain", "Collapsed, not responding", "Chest pain and sweating"],
    IncidentType.BREATHING: ["Cannot breathe properly", "Asthma attack", "Choking"],
    IncidentType.OTHER: ["High fever and fits", "Fainted in the market", "Snake bite"],
    IncidentType.PREGNANCY: ["Labour pain", "Pregnant woman bleeding"],
    IncidentType.FIRE: ["House fire, people burnt", "Gas cylinder blast"],
}


def ensure_database() -> None:
    """Create the target database if it does not exist yet."""
    mav_settings = get_settings()
    mav_server = create_engine(mav_settings.server_url, isolation_level="AUTOCOMMIT")
    with mav_server.connect() as mav_conn:
        mav_conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{mav_settings.db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    mav_server.dispose()
    print(f"  database `{mav_settings.db_name}` ready")


def seed_history(session, calls: int = HISTORY_CALLS, days: int = HISTORY_DAYS) -> int:
    """Load `calls` finished calls from the last `days` days. Returns the rows written.

    Every call is resolved: the incident is RESOLVED and its dispatch COMPLETED,
    with the ambulance and the hospital it used. Each call gets the nearest
    ambulance that was free at that time, starting from its station, and the
    hospital nearest to the patient, the same rules the live system follows. No
    ambulance is ever on two calls at once. A few road accidents and fires have
    several patients, stored as linked rows like a live call with several
    patients. The same seed always gives the same history.
    """
    mav_rng = random.Random(2026)
    mav_speed = get_settings().avg_speed_kmph
    mav_ambulances = session.execute(select(Ambulance).order_by(Ambulance.id)).scalars().all()
    mav_hospitals = session.execute(select(Hospital)).scalars().all()
    mav_area = get_settings()
    mav_lats = [mav_s[2] for mav_s in STATIONS]
    mav_lons = [mav_s[3] for mav_s in STATIONS]

    def where() -> tuple[float, float]:
        mav_pick = mav_rng.random()
        for mav_lat, mav_lon, mav_share in HOTSPOTS:
            if mav_pick < mav_share:
                mav_point = (mav_rng.gauss(mav_lat, SPREAD_DEGREES), mav_rng.gauss(mav_lon, SPREAD_DEGREES))
                break
            mav_pick -= mav_share
        else:
            mav_point = (mav_rng.uniform(min(mav_lats), max(mav_lats)),
                         mav_rng.uniform(min(mav_lons), max(mav_lons)))
        return (
            min(max(mav_point[0], mav_area.service_area_lat_min), mav_area.service_area_lat_max),
            min(max(mav_point[1], mav_area.service_area_lon_min), mav_area.service_area_lon_max),
        )

    mav_now = datetime.now().replace(microsecond=0)
    mav_start = mav_now - timedelta(days=days)
    # Every call finished at least two hours ago.
    mav_times = sorted(
        mav_start + timedelta(seconds=mav_rng.uniform(0, (days * 24 - 2) * 3600)) for _ in range(calls)
    )
    mav_free_at = {mav_amb.id: mav_start for mav_amb in mav_ambulances}
    mav_rows = 0

    for mav_number, mav_reported in enumerate(mav_times, start=1):
        mav_lat, mav_lon = where()
        mav_type = mav_rng.choices(list(TYPE_SHARES), weights=list(TYPE_SHARES.values()))[0]
        mav_severity = mav_rng.choices(list(SEVERITY_SHARES), weights=list(SEVERITY_SHARES.values()))[0]
        mav_patients = 1
        if mav_type in (IncidentType.ROAD_ACCIDENT, IncidentType.FIRE) and mav_rng.random() < 0.06:
            mav_patients = mav_rng.randint(2, 4)
        mav_main = Incident(
            reported_at=mav_reported,
            caller_phone=f"SIM-{mav_number:06d}",
            description=mav_rng.choice(DESCRIPTIONS[mav_type]),
            lat=mav_lat,
            lon=mav_lon,
            incident_type=mav_type,
            severity=mav_severity,
            status=IncidentStatus.RESOLVED,
            report_count=mav_rng.choices([1, 2, 3, 4], weights=[70, 18, 8, 4])[0],
            patients=mav_patients,
        )
        session.add(mav_main)
        session.flush()   # the further patients need this row's id
        mav_group = [mav_main] + [
            Incident(
                reported_at=mav_reported,
                caller_phone=mav_main.caller_phone,
                description=mav_main.description,
                lat=mav_lat,
                lon=mav_lon,
                incident_type=mav_type,
                severity=mav_severity,
                status=IncidentStatus.RESOLVED,
                parent_id=mav_main.id,
            )
            for _ in range(mav_patients - 1)
        ]
        session.add_all(mav_group[1:])
        session.flush()

        mav_hospital = min(mav_hospitals, key=lambda mav_h: haversine_km(mav_lat, mav_lon, mav_h.lat, mav_h.lon))
        mav_trip = eta_minutes(haversine_km(mav_lat, mav_lon, mav_hospital.lat, mav_hospital.lon), mav_speed)
        for mav_incident in mav_group:
            # Assigned within a minute or two of the call: the nearest
            # ambulance that is free by then.
            mav_assigned = mav_reported + timedelta(minutes=mav_rng.uniform(0.1, 1.5))
            mav_free = [mav_amb for mav_amb in mav_ambulances if mav_free_at[mav_amb.id] <= mav_assigned]
            if not mav_free:
                mav_first = min(mav_ambulances, key=lambda mav_amb: mav_free_at[mav_amb.id])
                mav_assigned = mav_free_at[mav_first.id]
                mav_free = [mav_first]
            mav_amb = min(mav_free, key=lambda mav_a: haversine_km(mav_a.base_lat, mav_a.base_lon, mav_lat, mav_lon))
            mav_km = haversine_km(mav_amb.base_lat, mav_amb.base_lon, mav_lat, mav_lon)
            mav_eta = eta_minutes(mav_km, mav_speed)
            mav_done = mav_assigned + timedelta(minutes=0.5 + mav_eta + 5.0 + mav_trip + 5.0)
            mav_free_at[mav_amb.id] = mav_done
            session.add(Dispatch(
                incident_id=mav_incident.id,
                ambulance_id=mav_amb.id,
                hospital_id=mav_hospital.id,
                assigned_at=mav_assigned,
                phase_started_at=mav_done,
                completed_at=mav_done,
                distance_km=round(mav_km, 3),
                eta_minutes=round(mav_eta, 1),
                # Wait plus drive. These times are ordinary minutes, not
                # compressed like a live call's.
                response_minutes=round(
                    (mav_assigned - mav_reported).total_seconds() / 60.0 + mav_eta, 1
                ),
                status=DispatchStatus.COMPLETED,
            ))
            mav_rows += 1
    session.commit()
    return mav_rows


def main() -> int:
    mav_parser = argparse.ArgumentParser(description="Seed the dispatch database.")
    mav_parser.add_argument(
        "--reset", action="store_true", help="drop all tables before seeding"
    )
    mav_parser.add_argument(
        "--no-history", action="store_true",
        help="leave the incidents and dispatches tables empty (used by the benchmarks)",
    )
    mav_args = mav_parser.parse_args()

    mav_settings = get_settings()
    print(f"Connecting to MySQL at {mav_settings.db_host}:{mav_settings.db_port} "
          f"as {mav_settings.db_user}")

    try:
        ensure_database()
    except Exception as exc:  # noqa: BLE001 - surface the real cause to the user
        print(f"\n  Could not connect to MySQL: {exc}\n", file=sys.stderr)
        print("  Check DB_USER and DB_PASSWORD in your .env file.", file=sys.stderr)
        return 1

    # Imported here so the engine is built after the database exists.
    from app.db import SessionLocal, engine

    if mav_args.reset:
        # The rebuilt tables number their rows from 1 again, so a queue entry
        # left in Redis from before would name a different incident. Cleared
        # first: if Redis does not answer, nothing has been touched yet.
        if shared.enabled():
            try:
                print(f"  Redis: {shared.clear_namespace()} keys of this database cleared")
            except shared.REDIS_ERRORS as exc:
                print(f"\n  Redis did not answer ({exc}); nothing was reset.", file=sys.stderr)
                print("  Start Redis (redis-server) and run the reset again.\n", file=sys.stderr)
                return 1
        Base.metadata.drop_all(engine)
        print("  existing tables dropped")

    Base.metadata.create_all(engine)
    print(f"  tables ready: {', '.join(sorted(Base.metadata.tables))}")

    with SessionLocal() as mav_session:
        mav_existing = mav_session.execute(
            select(func.count()).select_from(Ambulance)
        ).scalar_one()

        if mav_existing:
            print(f"\n  Already seeded ({mav_existing} ambulances). "
                  "Use --reset to rebuild.")
            return 0

        mav_session.add_all(
            Ambulance(
                call_sign=call_sign,
                station_name=mav_station,
                base_lat=lat,
                base_lon=lon,
                current_lat=lat,
                current_lon=lon,
                status=AmbulanceStatus.AVAILABLE,
            )
            for call_sign, mav_station, lat, lon in STATIONS
        )
        mav_session.add_all(
            Hospital(name=name, lat=lat, lon=lon, bed_capacity=mav_beds)
            for name, lat, lon, mav_beds in HOSPITALS
        )
        mav_session.commit()

        mav_history = 0
        if not mav_args.no_history:
            mav_history = seed_history(mav_session)

    print(f"\n  Seeded {len(STATIONS)} ambulances and {len(HOSPITALS)} hospitals "
          "across Dehradun.")
    if mav_history:
        print(f"  Loaded {HISTORY_CALLS} past calls from the last {HISTORY_DAYS} days "
              f"({mav_history} dispatches).")
    print("  Start the server with:  uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
