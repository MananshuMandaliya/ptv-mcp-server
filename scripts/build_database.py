"""
Builds a single SQLite database from Victoria's GTFS Schedule dataset.

Victoria's GTFS.zip is structured as one subfolder per operational branch
(e.g. "2/" for metro train, "3/" for metro tram, "4/" for metro bus, etc.),
each containing its own set of standard GTFS files (stops.txt, routes.txt,
trips.txt, stop_times.txt, calendar.txt). This script walks every subfolder,
tags each row with the branch it came from, and loads everything into a
single unified set of tables so the MCP server can query across all modes
at once.

Usage:
    1. Download the current GTFS.zip from the Transport Victoria Open Data
       Portal: https://opendata.transport.vic.gov.au/dataset/gtfs-schedule
    2. Place it at data/gtfs.zip (relative to the project root)
    3. Run: uv run python scripts/build_database.py
"""

import io
import sqlite3
import zipfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GTFS_ZIP_PATH = PROJECT_ROOT / "data" / "gtfs.zip"
DB_PATH = PROJECT_ROOT / "data" / "ptv.db"

# The core GTFS files we care about for this project. GTFS also defines
# shapes.txt, transfers.txt, pathways.txt, levels.txt etc., but those aren't
# needed for the natural-language queries this server supports.
GTFS_TABLES = {
    "routes.txt": "routes",
    "stops.txt": "stops",
    "trips.txt": "trips",
    "stop_times.txt": "stop_times",
    "calendar.txt": "calendar",
}

# Human-readable labels for Victoria's operational branch folder numbers.
# (Sourced from the DTP GTFS release notes; update if PTV renumbers branches.)
BRANCH_LABELS = {
    "1": "regional_train",
    "2": "metro_train",
    "3": "metro_tram",
    "4": "metro_bus",
    "5": "regional_coach",
    "6": "regional_bus",
    "10": "skybus",
    "11": "night_bus",
}


def find_branch_folders(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map top-level folder name -> branch label for every folder in the zip."""
    top_level_dirs = set()
    for name in zf.namelist():
        parts = Path(name).parts
        if len(parts) > 1:
            top_level_dirs.add(parts[0])
    return {d: BRANCH_LABELS.get(d, f"branch_{d}") for d in sorted(top_level_dirs)}


def open_branch_zip(outer_zf: zipfile.ZipFile, folder: str) -> zipfile.ZipFile | None:
    """Each branch folder in Victoria's GTFS.zip contains a nested
    google_transit.zip rather than raw .txt files. Open it in memory
    (no need to extract to disk) and return a ZipFile over it.
    """
    nested_path = f"{folder}/google_transit.zip"
    if nested_path not in outer_zf.namelist():
        return None
    return zipfile.ZipFile(io.BytesIO(outer_zf.read(nested_path)))


def load_gtfs_file(inner_zf: zipfile.ZipFile, filename: str) -> pd.DataFrame | None:
    if filename not in inner_zf.namelist():
        return None
    with inner_zf.open(filename) as f:
        return pd.read_csv(f, low_memory=False)


def build() -> None:
    if not GTFS_ZIP_PATH.exists():
        raise FileNotFoundError(
            f"Expected GTFS zip at {GTFS_ZIP_PATH}. Download it from "
            "https://opendata.transport.vic.gov.au/dataset/gtfs-schedule "
            "and save it there first."
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)

    with zipfile.ZipFile(GTFS_ZIP_PATH) as zf:
        branches = find_branch_folders(zf)
        print(f"Found {len(branches)} branches: {branches}")

        combined: dict[str, list[pd.DataFrame]] = {t: [] for t in GTFS_TABLES.values()}

        for folder, label in branches.items():
            inner_zf = open_branch_zip(zf, folder)
            if inner_zf is None:
                print(f"  (no google_transit.zip found in branch {folder}, skipping)")
                continue
            with inner_zf:
                for filename, table_name in GTFS_TABLES.items():
                    df = load_gtfs_file(inner_zf, filename)
                    if df is None:
                        continue
                    df["gtfs_mode"] = label
                    combined[table_name].append(df)

        for table_name, frames in combined.items():
            if not frames:
                print(f"  (no data found for {table_name}, skipping)")
                continue
            full_df = pd.concat(frames, ignore_index=True)
            full_df.to_sql(table_name, conn, if_exists="replace", index=False)
            print(f"  loaded {table_name}: {len(full_df):,} rows")

    # Indexes that make the server's queries fast instead of full-table-scanning
    # a stop_times table that can easily be several million rows.
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stop_times_stop_id ON stop_times(stop_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stop_times_trip_id ON stop_times(trip_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trips_route_id ON trips(route_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_trips_service_id ON trips(service_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stops_stop_name ON stops(stop_name)")
    conn.commit()

    # Precomputed rollups. GROUP BY over the full stop_times table (~12M
    # rows) at query time took 10+ seconds in testing -- long enough to
    # time out an MCP client. Computing these once at build time turns
    # "busiest stop/route" questions into a lookup against a table with
    # only as many rows as there are stops/routes (tens of thousands, not
    # millions), which is effectively instant.
    print("Building rollup tables...")
    cur.execute("DROP TABLE IF EXISTS route_trip_counts")
    cur.execute(
        """
        CREATE TABLE route_trip_counts AS
        SELECT r.route_id, r.route_short_name, r.gtfs_mode, COUNT(*) AS trip_count
        FROM trips t
        JOIN routes r ON r.route_id = t.route_id
        GROUP BY r.route_id
        """
    )
    cur.execute("CREATE INDEX idx_route_trip_counts_count ON route_trip_counts(trip_count)")

    cur.execute("DROP TABLE IF EXISTS stop_departure_counts")
    cur.execute(
        """
        CREATE TABLE stop_departure_counts AS
        SELECT s.stop_id, s.stop_name, s.gtfs_mode, COUNT(*) AS departure_count
        FROM stop_times st
        JOIN stops s ON s.stop_id = st.stop_id
        GROUP BY s.stop_id
        """
    )
    cur.execute("CREATE INDEX idx_stop_departure_counts_count ON stop_departure_counts(departure_count)")
    conn.commit()
    print("  done")

    conn.close()

    print(f"\nDone. Database written to {DB_PATH}")


if __name__ == "__main__":
    build()