# PTV Transit MCP Server

![demo](docs/demo.gif)

An [MCP](https://modelcontextprotocol.io) server that exposes Victoria's public transport (PTV) GTFS timetable data — trains, trams, and buses — so an LLM (Claude Desktop, Cursor, etc.) can answer natural-language questions about routes, stops, and departure times.

**Data at a glance:** 1,069 routes · 31,971 stops · 333,875 trips · 12.6 million scheduled stop times, across 8 transport modes (metro train, metro tram, metro bus, regional train, regional bus, regional coach, night bus, SkyBus).

## Why this project

Data source: [Transport Victoria's GTFS Schedule dataset](https://opendata.transport.vic.gov.au/dataset/gtfs-schedule), published by the Victorian Department of Transport and Planning. It contains static timetable information for all metropolitan and regional trains, buses (including coach), and trams in Victoria, refreshed on a weekly (or as-needed) basis.

This project turns that raw GTFS export into a queryable SQLite database and wraps it in an MCP server with:
- **Tools** for structured lookups (stop search, route listing, next departures)
- **A guarded raw-SQL tool** for open-ended questions, restricted to read-only `SELECT` statements with a hard file-level read-only guarantee underneath
- **A schema resource** so the model can write informed queries without guessing column names
- **A prompt template** for trip planning

## Setup (Windows, using `uv`)

1. **Install `uv`** if you don't have it:
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

2. **Clone this repo and install dependencies:**
   ```powershell
   git clone https://github.com/MananshuMandaliya/ptv-mcp-server.git
   cd ptv-mcp-server
   uv sync
   ```

3. **Download the GTFS data:**
   Go to the [GTFS Schedule dataset page](https://opendata.transport.vic.gov.au/dataset/gtfs-schedule), download the current `GTFS.zip`, and save it as:
   ```
   data/gtfs.zip
   ```

4. **Build the database:**
   ```powershell
   uv run python scripts/build_database.py
   ```
   This creates `data/ptv.db`. It can take a couple of minutes — `stop_times` alone typically runs into the millions of rows across all modes.

5. **Test it with the MCP Inspector:**
   ```powershell
   uv run mcp dev src/ptv_mcp/server.py
   ```
   This opens an interactive UI in your browser where you can call each tool directly and see the raw responses.

6. **Install into Claude Desktop:**
   ```powershell
   uv run mcp install src/ptv_mcp/server.py
   ```
   Restart Claude Desktop, and you should see this server's tools available in a new conversation.

## Example queries once connected

- "What time is the next train from Oakleigh station?"
- "List all tram routes."
- "How many stops does the Frankston line have?" (via the raw SQL tool)

![example query screenshot](docs/query-screenshot.png)

## Project structure

```
ptv-mcp-server/
├── pyproject.toml
├── scripts/build_database.py   # GTFS.zip -> SQLite
└── src/ptv_mcp/server.py       # the MCP server
```

## Design decisions

**Why SQLite instead of a hosted database?** Zero setup for anyone reviewing this project — clone, build, run. No credentials, no hosting cost, no server to keep alive. GTFS is also a natural fit for SQLite: it's a static, read-heavy dataset that's rebuilt from source on a schedule rather than written to at runtime.

**Why a nested-zip loader?** Victoria's GTFS.zip isn't structured as one folder per mode with plain `.txt` files — each branch folder (train, tram, bus, etc.) contains its own nested `google_transit.zip`. The first version of the loader assumed flat `.txt` files and silently loaded zero rows. Rather than guessing at a fix, I wrote a small diagnostic script to print the actual zip structure, confirmed the nested-zip pattern, then rewrote the loader to open each nested zip in memory (`io.BytesIO`) rather than extracting to disk.

**Why guard `run_sql_query` at two layers?** The tool needs to let the model run arbitrary read-only SQL for questions the structured tools don't anticipate, but "arbitrary SQL from an LLM" is a real risk surface. So there are two independent layers: the SQLite connection itself is opened via a read-only URI (`file:...?mode=ro`), which SQLite enforces at the file-handle level regardless of what the query says, and a regex guard rejects anything that isn't a single `SELECT` statement before it's even executed. Either layer alone would probably be enough; both together means one bug in the regex doesn't turn into a write.

**Why index `stop_id`, `trip_id`, `route_id`, and `service_id`?** `stop_times` has 12.6 million rows. Without indexes, `get_next_departures` was a full table scan on every call. With them, lookups return in well under a second even on this dataset size.

## Notes / limitations

- This serves the **static schedule**, not live GPS positions or real-time delays (Transport Victoria separately publishes a GTFS-Realtime feed for that — a natural extension of this project).
- The GTFS export contains a rolling window of timetable data from its export date, so `data/gtfs.zip` should be re-downloaded periodically to stay current.