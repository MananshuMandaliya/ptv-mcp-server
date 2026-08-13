"""
An MCP server exposing Victoria's public transport (PTV) GTFS timetable
data, so an LLM can answer natural-language questions like "when's the next
tram from Southern Cross" or "what train routes stop near Oakleigh".

Run locally with the MCP Inspector:
    uv run mcp dev src/ptv_mcp/server.py

Or install into Claude Desktop:
    uv run mcp install src/ptv_mcp/server.py
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ptv.db"

mcp = FastMCP("PTV Transit Data")


def get_connection() -> sqlite3.Connection:
    """Opens the database in read-only mode via a SQLite URI.

    This is a hard safety boundary, not just an application-level check:
    even if a query somehow slipped past the guard in run_sql_query, SQLite
    itself will refuse any write at the OS/file-mode level.
    """
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


@mcp.tool()
def search_stops(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search for public transport stops/stations by name.

    Args:
        query: Partial or full stop name to search for, e.g. "Flinders" or "Oakleigh".
        limit: Maximum number of results to return.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT DISTINCT stop_id, stop_name, stop_lat, stop_lon, gtfs_mode
            FROM stops
            WHERE stop_name LIKE ?
            ORDER BY stop_name
            LIMIT ?
            """,
            (f"%{query}%", limit),
        )
        return rows_to_dicts(cur.fetchall())
    finally:
        conn.close()


@mcp.tool()
def list_routes(mode: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List public transport routes, optionally filtered by mode.

    Args:
        mode: One of "metro_train", "metro_tram", "metro_bus", "regional_train",
              "regional_bus", "regional_coach", "skybus", "night_bus". Omit to
              list across all modes.
        limit: Maximum number of results to return.
    """
    conn = get_connection()
    try:
        if mode:
            cur = conn.execute(
                """
                SELECT DISTINCT route_id, route_short_name, route_long_name, gtfs_mode
                FROM routes
                WHERE gtfs_mode = ?
                ORDER BY route_short_name
                LIMIT ?
                """,
                (mode, limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT DISTINCT route_id, route_short_name, route_long_name, gtfs_mode
                FROM routes
                ORDER BY gtfs_mode, route_short_name
                LIMIT ?
                """,
                (limit,),
            )
        return rows_to_dicts(cur.fetchall())
    finally:
        conn.close()


@mcp.tool()
def get_next_departures(stop_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Get upcoming scheduled departures for a given stop, across all routes
    serving it.

    Note: this reads the static GTFS schedule (not live GPS positions), so
    results reflect the timetable, not real-time delays.

    Args:
        stop_name: Name (or partial name) of the stop/station to look up.
        limit: Maximum number of departures to return.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT
                s.stop_name,
                r.route_short_name,
                r.route_long_name,
                st.departure_time,
                t.trip_headsign,
                r.gtfs_mode
            FROM stop_times st
            JOIN stops s ON s.stop_id = st.stop_id
            JOIN trips t ON t.trip_id = st.trip_id
            JOIN routes r ON r.route_id = t.route_id
            WHERE s.stop_name LIKE ?
            ORDER BY st.departure_time
            LIMIT ?
            """,
            (f"%{stop_name}%", limit),
        )
        return rows_to_dicts(cur.fetchall())
    finally:
        conn.close()


# Only these statements are permitted in run_sql_query. Anything else
# (INSERT, UPDATE, DELETE, DROP, ATTACH, PRAGMA, etc.) is rejected before
# it ever reaches SQLite — this is a defense-in-depth layer on top of the
# read-only file handle in get_connection().
_SELECT_ONLY = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|PRAGMA|VACUUM|REPLACE)\b",
    re.IGNORECASE,
)


@mcp.tool()
def run_sql_query(query: str) -> list[dict[str, Any]]:
    """Run a read-only SQL SELECT query against the GTFS database directly,
    for questions the other tools don't cover.

    Only SELECT statements are permitted. Available tables: routes, stops,
    trips, stop_times, calendar (each has a gtfs_mode column identifying the
    transport mode). Use the ptv://schema resource to see column details.

    Args:
        query: A single SQL SELECT statement.
    """
    if not _SELECT_ONLY.match(query):
        raise ValueError("Only SELECT statements are permitted.")
    if _FORBIDDEN_KEYWORDS.search(query):
        raise ValueError("Query contains a disallowed keyword.")
    if ";" in query.strip().rstrip(";"):
        raise ValueError("Only a single statement is permitted per query.")

    conn = get_connection()
    try:
        cur = conn.execute(query)
        rows = cur.fetchmany(200)  # hard cap to avoid dumping huge result sets
        return rows_to_dicts(rows)
    finally:
        conn.close()


@mcp.resource("ptv://schema")
def get_schema() -> str:
    """Returns the column names and types for every table in the database,
    so the model can write informed run_sql_query calls without guessing
    at column names.
    """
    conn = get_connection()
    try:
        tables = ["routes", "stops", "trips", "stop_times", "calendar"]
        lines = []
        for table in tables:
            cur = conn.execute(f"PRAGMA table_info({table})")
            cols = cur.fetchall()
            if not cols:
                continue
            col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols)
            lines.append(f"{table}: {col_desc}")
        return "\n\n".join(lines)
    finally:
        conn.close()


@mcp.prompt()
def trip_planner(origin: str, destination: str) -> str:
    """A reusable prompt template for planning a trip between two stops."""
    return (
        f"I want to travel from {origin} to {destination} using Victoria's "
        "public transport. Use the search_stops and get_next_departures "
        "tools to find relevant stops and upcoming services, and suggest "
        "the best route, including where I might need to change services."
    )


if __name__ == "__main__":
    mcp.run()
