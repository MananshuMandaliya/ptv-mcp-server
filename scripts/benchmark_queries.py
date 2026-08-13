"""
Times representative queries directly against data/ptv.db, bypassing Claude
Desktop entirely. This isolates whether slowness is in the database layer
(fixable with indexes) or just normal LLM round-trip overhead (expected,
not something we can speed up).

Usage:
    uv run python scripts/benchmark_queries.py
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ptv.db"


def timed(label: str, conn: sqlite3.Connection, query: str, params: tuple = ()):
    start = time.perf_counter()
    cur = conn.execute(query, params)
    rows = cur.fetchall()
    elapsed = time.perf_counter() - start
    print(f"{label}: {elapsed*1000:.1f} ms  ({len(rows)} rows)")
    return elapsed


def explain(conn: sqlite3.Connection, query: str, params: tuple = ()):
    cur = conn.execute(f"EXPLAIN QUERY PLAN {query}", params)
    for row in cur.fetchall():
        print("   plan:", dict(row))


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print("=== Structured tool-style queries ===\n")

    timed(
        "search_stops('Oakleigh')",
        conn,
        "SELECT * FROM stops WHERE stop_name LIKE ? LIMIT 15",
        ("%Oakleigh%",),
    )

    timed(
        "get_next_departures('Oakleigh')",
        conn,
        """
        SELECT s.stop_name, r.route_short_name, st.departure_time
        FROM stop_times st
        JOIN stops s ON s.stop_id = st.stop_id
        JOIN trips t ON t.trip_id = st.trip_id
        JOIN routes r ON r.route_id = t.route_id
        WHERE s.stop_name LIKE ?
        ORDER BY st.departure_time
        LIMIT 10
        """,
        ("%Oakleigh%",),
    )

    print("\n=== Open-ended analytics (the likely slow ones) ===\n")

    t1 = timed(
        "busiest route by trip count",
        conn,
        """
        SELECT r.route_short_name, COUNT(*) as trip_count
        FROM trips t
        JOIN routes r ON r.route_id = t.route_id
        GROUP BY r.route_id
        ORDER BY trip_count DESC
        LIMIT 5
        """,
    )
    explain(
        conn,
        """
        SELECT r.route_short_name, COUNT(*) as trip_count
        FROM trips t
        JOIN routes r ON r.route_id = t.route_id
        GROUP BY r.route_id
        ORDER BY trip_count DESC
        LIMIT 5
        """,
    )

    print()
    t2 = timed(
        "station with most departures",
        conn,
        """
        SELECT s.stop_name, COUNT(*) as departure_count
        FROM stop_times st
        JOIN stops s ON s.stop_id = st.stop_id
        GROUP BY s.stop_id
        ORDER BY departure_count DESC
        LIMIT 5
        """,
    )
    explain(
        conn,
        """
        SELECT s.stop_name, COUNT(*) as departure_count
        FROM stop_times st
        JOIN stops s ON s.stop_id = st.stop_id
        GROUP BY s.stop_id
        ORDER BY departure_count DESC
        LIMIT 5
        """,
    )

    conn.close()

    print("\n=== Interpretation ===")
    if t1 > 1.0 or t2 > 1.0:
        print(
            "The analytics queries above are genuinely slow at the database "
            "layer (SCAN instead of SEARCH in the query plan means a full "
            "table scan). This is fixable with additional indexes."
        )
    else:
        print(
            "All queries returned in well under a second. If Claude Desktop "
            "still feels slow, that's very likely normal LLM round-trip time "
            "(model reasoning + tool call formatting), not the database."
        )


if __name__ == "__main__":
    main()