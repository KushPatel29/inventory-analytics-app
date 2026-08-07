from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
import pandas as pd


DB_PATH = os.path.join(os.getcwd(), "data", "app.db")


def _ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                run_id INTEGER,
                key TEXT,
                value TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(id)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS sku_stats (
                run_id INTEGER,
                data BLOB
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS holding_cost (
                run_id INTEGER,
                data BLOB
            )
            """
        )


def _df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    # Use pandas parquet via pyarrow if installed; otherwise fallback to CSV bytes
    try:
        import pyarrow as pa  # noqa: F401
        import io
        import pyarrow.parquet as pq
        table = pa.Table.from_pandas(df)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return buf.getvalue()
    except Exception:
        return df.to_csv(index=False).encode("utf-8")


def _bytes_to_df(data: bytes) -> pd.DataFrame:
    # Best-effort decode: try parquet then CSV
    try:
        import pyarrow.parquet as pq
        import io
        import pyarrow as pa  # noqa: F401
        buf = io.BytesIO(data)
        table = pq.read_table(buf)
        return table.to_pandas()
    except Exception:
        import io
        return pd.read_csv(io.BytesIO(data))


def save_run(sku_stats: pd.DataFrame, holding_cost: pd.DataFrame, params: dict) -> int:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute("INSERT INTO runs(created_at) VALUES (?)", (datetime.utcnow().isoformat(),))
        run_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO meta(run_id, key, value) VALUES (?,?,?)",
            [(run_id, k, json.dumps(v)) for k, v in params.items()],
        )
        cur.execute(
            "INSERT INTO sku_stats(run_id, data) VALUES (?,?)",
            (run_id, _df_to_parquet_bytes(sku_stats)),
        )
        cur.execute(
            "INSERT INTO holding_cost(run_id, data) VALUES (?,?)",
            (run_id, _df_to_parquet_bytes(holding_cost)),
        )
        con.commit()
        return int(run_id)


def list_runs() -> list[dict]:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        rows = cur.execute("SELECT id, created_at FROM runs ORDER BY id DESC").fetchall()
        return [{"id": r[0], "created_at": r[1]} for r in rows]


def load_run(run_id: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        meta_rows = cur.execute("SELECT key, value FROM meta WHERE run_id=?", (run_id,)).fetchall()
        params = {k: json.loads(v) for k, v in meta_rows}
        sku_blob = cur.execute("SELECT data FROM sku_stats WHERE run_id=?", (run_id,)).fetchone()
        hc_blob = cur.execute("SELECT data FROM holding_cost WHERE run_id=?", (run_id,)).fetchone()
        if not sku_blob or not hc_blob:
            raise ValueError("Run not found or incomplete")
        sku_stats = _bytes_to_df(sku_blob[0])
        holding_cost = _bytes_to_df(hc_blob[0])
        return sku_stats, holding_cost, params

