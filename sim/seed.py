"""Create the database, build the schema, and load reference data.

Credentials are read from your local .env file -- nothing is hard-coded here.

    python -m sim.seed            # create + seed (skips if already populated)
    python -m sim.seed --reset    # drop everything and rebuild
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, func, select, text

from app.config import get_settings
from app.models import Ambulance, AmbulanceStatus, Base, Hospital

# Service area: Dehradun, Uttarakhand. Coordinates are approximate positions
# of real landmarks, used as ambulance standby points.
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
]

HOSPITALS: list[tuple[str, float, float, int]] = [
    ("Doon Government Medical College Hospital", 30.3245, 78.0436, 500),
    ("Shri Mahant Indiresh Hospital",            30.3006, 78.0163, 750),
    ("Max Super Speciality Hospital",            30.3629, 78.0708, 200),
    ("Synergy Institute of Medical Sciences",    30.2872, 77.9981, 300),
]


def ensure_database() -> None:
    """Create the target database if it does not exist yet."""
    settings = get_settings()
    server = create_engine(settings.server_url, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    server.dispose()
    print(f"  database `{settings.db_name}` ready")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the dispatch database.")
    parser.add_argument(
        "--reset", action="store_true", help="drop all tables before seeding"
    )
    args = parser.parse_args()

    settings = get_settings()
    print(f"Connecting to MySQL at {settings.db_host}:{settings.db_port} "
          f"as {settings.db_user}")

    try:
        ensure_database()
    except Exception as exc:  # noqa: BLE001 - surface the real cause to the user
        print(f"\n  Could not connect to MySQL: {exc}\n", file=sys.stderr)
        print("  Check DB_USER and DB_PASSWORD in your .env file.", file=sys.stderr)
        return 1

    # Imported here so the engine is built after the database exists.
    from app.db import SessionLocal, engine

    if args.reset:
        Base.metadata.drop_all(engine)
        print("  existing tables dropped")

    Base.metadata.create_all(engine)
    print(f"  tables ready: {', '.join(sorted(Base.metadata.tables))}")

    with SessionLocal() as session:
        existing = session.execute(
            select(func.count()).select_from(Ambulance)
        ).scalar_one()

        if existing:
            print(f"\n  Already seeded ({existing} ambulances). "
                  "Use --reset to rebuild.")
            return 0

        session.add_all(
            Ambulance(
                call_sign=call_sign,
                station_name=station,
                base_lat=lat,
                base_lon=lon,
                current_lat=lat,
                current_lon=lon,
                status=AmbulanceStatus.AVAILABLE,
            )
            for call_sign, station, lat, lon in STATIONS
        )
        session.add_all(
            Hospital(name=name, lat=lat, lon=lon, bed_capacity=beds)
            for name, lat, lon, beds in HOSPITALS
        )
        session.commit()

    print(f"\n  Seeded {len(STATIONS)} ambulances and {len(HOSPITALS)} hospitals "
          "across Dehradun.")
    print("  Start the server with:  uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
