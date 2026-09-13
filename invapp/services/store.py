"""
Run history: what the headline numbers were each time a workbook was ingested.

The previous version stored the full derived frames as blobs. That was storage
in search of a use - nothing ever read them back except a "load run" button
that put stale numbers on a live page. What is actually worth keeping is the
much smaller thing: the KPIs per run, so the fifth Monday of doing this shows
whether the stockout count and days-of-cover are moving, which is the question
a weekly planning cycle exists to answer.

Storage is SQLite on local disk. On the hosted demo the container filesystem is
ephemeral, so history there lasts as long as the process - which is honest for
a demo and stated on the page rather than hidden behind an empty chart.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("INVAPP_DB_PATH") or os.path.join(os.getcwd(), "data", "app.db")

# The KPIs a run is worth remembering by. Kept as an explicit list rather than
# "whatever is in the headline dict": the dict grows, and a schema that grows
# with it turns every new metric into a migration.
TRACKED = (
    "InventoryValueUSD",
    "InventoryTurns",
    "DaysInventoryOutstanding",
    "FillRatePct",
    "StockoutCount",
    "BelowReorderCount",
    "ForecastAccuracyPct",
    "RecordAccuracyPct",
    "ExcessValueUSD",
    "DeadStockValueUSD",
    "ReorderValueUSD",
    "SKUCount",
    "OpenActions",
)


SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        as_of TEXT,
        params TEXT,
        metrics TEXT
    )
    """
)


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(SCHEMA)
    # A database left over from an older version of this app has a `runs`
    # table with different columns, so CREATE TABLE IF NOT EXISTS is a no-op
    # and every query afterwards fails on a missing column. History here is
    # a convenience, not a record of anything, so an incompatible one is
    # dropped rather than migrated.
    columns = {row[1] for row in con.execute("PRAGMA table_info(runs)")}
    if not {"as_of", "metrics", "params"}.issubset(columns):
        logger.info("store.schema_reset")
        con.execute("DROP TABLE runs")
        con.execute(SCHEMA)
    return con


def save_run(model) -> int:
    """Record this run's headline metrics. Returns the run id."""
    metrics = {key: model.headline.get(key) for key in TRACKED}
    with _connect() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO runs(created_at, as_of, params, metrics) VALUES (?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(),
                str(model.as_of.date()),
                json.dumps(model.params, default=str),
                json.dumps(metrics, default=str),
            ),
        )
        con.commit()
        return int(cur.lastrowid)


def list_runs(limit: int = 30) -> list[dict]:
    """Recent runs, newest first, with their metrics flattened onto the row."""
    try:
        with _connect() as con:
            rows = con.execute(
                "SELECT id, created_at, as_of, metrics FROM runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except sqlite3.Error:
        logger.warning("store.list_runs_failed", exc_info=True)
        return []

    out = []
    for run_id, created_at, as_of, metrics in rows:
        row = {"id": run_id, "created_at": created_at, "as_of": as_of}
        try:
            row.update(json.loads(metrics or "{}"))
        except json.JSONDecodeError:
            pass
        out.append(row)
    return out


def clear_runs() -> None:
    """Drop the history. For tests."""
    try:
        with _connect() as con:
            con.execute("DELETE FROM runs")
            con.commit()
    except sqlite3.Error:
        logger.warning("store.clear_failed", exc_info=True)


__all__ = ["DB_PATH", "TRACKED", "clear_runs", "list_runs", "save_run"]
