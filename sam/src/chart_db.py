#!/usr/bin/env python3
"""
chart_db.py
===========
Minimal SQLite helper for charting rollups.

Series key: ``sensor_id`` column stores canonical ``point_id`` (e.g. machine-001:humidity_rh).
Optional ``asset_id`` / ``metric_id`` columns support filtered queries.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pipeline_config as config


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
        _migrate_chart_points_columns(conn)


def _migrate_chart_points_columns(conn: sqlite3.Connection) -> None:
    """Add multi-metric columns to existing chart_points tables."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(chart_points)")}
    for col, typ in (
        ("point_id", "TEXT"),
        ("asset_id", "TEXT"),
        ("metric_id", "TEXT"),
        ("unit", "TEXT"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE chart_points ADD COLUMN {col} {typ}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chart_points_asset_metric_ts "
        "ON chart_points(asset_id, metric_id, ts)"
    )


def resolve_chart_identity(
    sensor_id: str = "",
    metric_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[str]]:
    """
    Resolve chart series identity from probe id, point id, or asset+metric.

    Returns dict with point_id (stored as sensor_id), asset_id, metric_id, unit.
    """
    payload = payload or {}
    if payload:
        fields = {
            "point_id": payload.get("pointId"),
            "asset_id": payload.get("asset"),
            "metric_id": payload.get("metric"),
            "unit": payload.get("unit"),
            "legacy_probe": payload.get("sensorId"),
        }
    else:
        fields = {
            "point_id": None,
            "asset_id": asset_id,
            "metric_id": metric_id,
            "unit": None,
            "legacy_probe": sensor_id,
        }

    pid = (fields.get("point_id") or "").strip() or None
    aid = (fields.get("asset_id") or "").strip() or None
    mid = (fields.get("metric_id") or "").strip() or None
    probe = (fields.get("legacy_probe") or sensor_id or "").strip() or None

    sep = config.point_id_separator()
    if not pid and probe and sep in probe and not aid and not mid:
        parsed_aid, parsed_mid = probe.split(sep, 1)
        pid, aid, mid = probe, parsed_aid, parsed_mid

    if not pid and aid and mid:
        pid = config.make_point_id(aid, mid)
    if not pid and probe:
        from sensor_db import _demo_asset_metric_from_probe

        demo_asset, demo_metric = _demo_asset_metric_from_probe(probe)
        if demo_asset and demo_metric:
            aid = aid or demo_asset
            mid = mid or demo_metric
            pid = config.make_point_id(demo_asset, demo_metric)
        else:
            pid, resolved_asset, resolved_metric = config.resolve_point_id(
                {"sensorId": probe, "asset": aid, "metric": mid}, {}
            )
            if pid:
                aid = aid or resolved_asset
                mid = mid or resolved_metric

    if not pid and probe:
        pid = probe
    if pid and config.POINT_ID_SEPARATOR in pid:
        parsed_aid, parsed_mid = pid.split(config.point_id_separator(), 1)
        # Do not keep resolve_point_id mistakes (asset=full point id, metric=supply_temp_c default).
        if not aid or aid == pid or config.POINT_ID_SEPARATOR in aid:
            aid = parsed_aid
        mid = parsed_mid

    unit = fields.get("unit") or (config._metric_rule(mid or "").get("unit") if mid else None)
    return {
        "point_id": pid,
        "asset_id": aid,
        "metric_id": mid,
        "unit": str(unit) if unit else None,
    }


def _legacy_probe_id_for_point_id(point_id: Optional[str]) -> Optional[str]:
    """Map canonical point id to demo probe sensor_id stored in older chart rows."""
    pid = (point_id or "").strip()
    if not pid or config.POINT_ID_SEPARATOR not in pid:
        return None
    asset, metric = pid.split(config.point_id_separator(), 1)
    prefix = {"machine-001": "m1", "machine-002": "m2", "machine-003": "m3"}.get(asset)
    if not prefix:
        return None
    if metric == "inlet_temp_c":
        return f"{prefix}-temp-inlet"
    if metric == "outlet_temp_c":
        return f"{prefix}-temp-outlet"
    if metric == "motor_temp_c":
        return f"{prefix}-temp-motor"
    if metric == "humidity_rh":
        return f"{prefix}-humidity"
    if metric == "motor_vibration_mm_s":
        return f"{prefix}-vibration"
    if metric == "supply_temp_c":
        return f"{prefix}-temp-motor"
    return None


def build_series_filter_sql(
    sensor_id: str = "",
    metric_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    *,
    sensor_col: str = "sensor_id",
    include_metric_column: bool = True,
) -> Tuple[str, List[Any]]:
    """
    Build SQL ``AND (...)` clause matching a chart series.

    Matches point_id/sensor_id, asset+metric pair, or demo probe ids (m1-humidity).
    """
    identity = resolve_chart_identity(sensor_id, metric_id, asset_id)
    clauses: List[str] = []
    params: List[Any] = []

    pid = identity.get("point_id")
    if pid:
        clauses.append(f"{sensor_col} = ?")
        params.append(pid)
        legacy_probe = _legacy_probe_id_for_point_id(pid)
        if legacy_probe and legacy_probe != pid:
            clauses.append(f"{sensor_col} = ?")
            params.append(legacy_probe)

    aid, mid = identity.get("asset_id"), identity.get("metric_id")
    if aid and mid:
        if include_metric_column:
            clauses.append("(asset_id = ? AND metric_id = ?)")
            params.extend([aid, mid])
        else:
            clauses.append(f"{sensor_col} = ?")
            params.append(config.make_point_id(aid, mid))

    if metric_id and include_metric_column and not mid:
        clauses.append("metric_id = ?")
        params.append(metric_id)

    if not clauses:
        clauses.append(f"{sensor_col} = ?")
        params.append(sensor_id or "")

    return f" AND ({' OR '.join(clauses)})", params


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


def write_point_and_rollups(
    ts: str,
    sensor_id: str,
    value: float,
    source: str,
    zone: str = None,
    *,
    point_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    metric_id: Optional[str] = None,
    unit: Optional[str] = None,
):
    identity = resolve_chart_identity(sensor_id, metric_id, asset_id)
    series_id = point_id or identity.get("point_id") or sensor_id
    if not series_id:
        return

    dt = _parse_ts(ts)
    b10s = _bucket_iso(dt, 10)
    b1m = _bucket_iso(dt, 60)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO chart_points
                (ts, sensor_id, value, zone, source, point_id, asset_id, metric_id, unit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                series_id,
                value,
                zone,
                source,
                series_id,
                identity.get("asset_id"),
                identity.get("metric_id"),
                unit or identity.get("unit"),
            ),
        )
        _upsert_rollup(conn, "chart_rollups_10s", b10s, series_id, source, value)
        _upsert_rollup(conn, "chart_rollups_1m", b1m, series_id, source, value)
