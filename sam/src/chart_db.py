#!/usr/bin/env python3
"""
chart_db.py
===========
Minimal SQLite helper for charting rollups.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


DB_PATH = os.getenv(
    "CHART_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "chart_data.db"),
)


def get_db_path() -> str:
    return os.path.abspath(DB_PATH)


@contextmanager
def get_connection():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database():
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chart_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                value REAL NOT NULL,
                zone TEXT,
                source TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chart_rollups_10s (
                bucket_ts TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                source TEXT NOT NULL,
                min_v REAL NOT NULL,
                max_v REAL NOT NULL,
                sum_v REAL NOT NULL,
                count_v INTEGER NOT NULL,
                last_v REAL NOT NULL,
                PRIMARY KEY (bucket_ts, sensor_id, source)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chart_rollups_1m (
                bucket_ts TEXT NOT NULL,
                sensor_id TEXT NOT NULL,
                source TEXT NOT NULL,
                min_v REAL NOT NULL,
                max_v REAL NOT NULL,
                sum_v REAL NOT NULL,
                count_v INTEGER NOT NULL,
                last_v REAL NOT NULL,
                PRIMARY KEY (bucket_ts, sensor_id, source)
            )
            """
        )

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chart_points_sensor_ts ON chart_points(sensor_id, ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rollups10_sensor_ts ON chart_rollups_10s(sensor_id, bucket_ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rollups1m_sensor_ts ON chart_rollups_1m(sensor_id, bucket_ts)"
        )


def _parse_ts(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(timezone.utc)


def _bucket_iso(dt: datetime, seconds: int) -> str:
    bucket_epoch = int(dt.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _upsert_rollup(conn: sqlite3.Connection, table: str, bucket_ts: str, sensor_id: str, source: str, value: float):
    conn.execute(
        f"""
        INSERT INTO {table} (bucket_ts, sensor_id, source, min_v, max_v, sum_v, count_v, last_v)
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(bucket_ts, sensor_id, source) DO UPDATE SET
            min_v = MIN(min_v, excluded.min_v),
            max_v = MAX(max_v, excluded.max_v),
            sum_v = sum_v + excluded.sum_v,
            count_v = count_v + 1,
            last_v = excluded.last_v
        """,
        (bucket_ts, sensor_id, source, value, value, value, value),
    )


def write_point_and_rollups(ts: str, sensor_id: str, value: float, source: str, zone: str = None):
    dt = _parse_ts(ts)
    b10s = _bucket_iso(dt, 10)
    b1m = _bucket_iso(dt, 60)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO chart_points (ts, sensor_id, value, zone, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, sensor_id, value, zone, source),
        )
        _upsert_rollup(conn, "chart_rollups_10s", b10s, sensor_id, source, value)
        _upsert_rollup(conn, "chart_rollups_1m", b1m, sensor_id, source, value)
