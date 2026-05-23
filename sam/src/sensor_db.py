#!/usr/bin/env python3
"""
sensor_db.py
============
SQLite database for sensor pipeline data.
Acts as the "time-series DB" for the demo.

Legacy tables (agents/demo):
  - sensor_readings, sketches, alerts, fleet_status

Canonical multi-metric tables (dual-written when TELEMETRY_DUAL_WRITE=true):
  - telemetry_readings, telemetry_sketches, telemetry_alerts, telemetry_fleet_status
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pipeline_config as config

# Database file location (override for tests or per-deployment paths)
DB_PATH = os.getenv(
    "SENSOR_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "sensor_data.db"),
)

TELEMETRY_DUAL_WRITE = os.getenv("TELEMETRY_DUAL_WRITE", "true").lower() in (
    "true",
    "1",
    "yes",
)
FLEET_QUERY_USE_TELEMETRY = os.getenv("FLEET_QUERY_USE_TELEMETRY", "true").lower() in (
    "true",
    "1",
    "yes",
)


def get_db_path() -> str:
    """Get the absolute path to the database file."""
    return os.path.abspath(DB_PATH)


def _resolve_identity(
    sensor_id: str,
    value: float,
    *,
    point_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    metric_id: Optional[str] = None,
) -> Tuple[str, str, str, float]:
    """Map legacy sensor_id / explicit fields to (point_id, asset_id, metric_id, value)."""
    payload: Dict[str, Any] = {"sensorId": sensor_id}
    if point_id:
        payload["pointId"] = point_id
    if asset_id:
        payload["asset"] = asset_id
    if metric_id:
        payload["metric"] = metric_id
    pid, aid, mid = config.resolve_point_id(payload, {})
    if not pid:
        pid = point_id or sensor_id
        aid = asset_id or sensor_id
        mid = metric_id or config.DEFAULT_METRIC_ID
    return pid, aid, mid, float(value)


def _metric_unit(metric_id: str) -> Optional[str]:
    unit = config._metric_rule(metric_id).get("unit")
    return str(unit) if unit else None


def fields_from_pipeline_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a filtered/sketched pipeline dict to DB write fields.

    Returns sensor_id (compat), point_id, asset_id, metric_id, value, unit.
    """
    value = config.observation_value(data)
    if value is None and data.get("temperature") is not None:
        value = float(data["temperature"])
    point_id, asset_id, metric_id = config.resolve_point_id(data, {})
    sensor_id = data.get("sensorId") or point_id
    if not point_id:
        point_id = sensor_id
    if not asset_id:
        asset_id = data.get("asset") or sensor_id
    if not metric_id:
        metric_id = data.get("metric") or config.DEFAULT_METRIC_ID
    unit = data.get("unit") or _metric_unit(metric_id)
    return {
        "sensor_id": sensor_id,
        "point_id": point_id,
        "asset_id": asset_id,
        "metric_id": metric_id,
        "value": value,
        "unit": unit,
    }


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database():
    """Initialize the database schema."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Readings that passed deadband filter
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                temperature REAL NOT NULL,
                timestamp TEXT NOT NULL,
                delta_percent REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Sketch summaries
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sketches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                temperature REAL NOT NULL,
                zone TEXT NOT NULL,
                sketch TEXT NOT NULL,
                trend TEXT,
                window_avg REAL,
                window_min REAL,
                window_max REAL,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Detected alerts/anomalies
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sensor_id TEXT NOT NULL,
                temperature REAL NOT NULL,
                zone TEXT NOT NULL,
                severity TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                description TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                acknowledged BOOLEAN DEFAULT FALSE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Fleet status snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fleet_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                active_sensors INTEGER NOT NULL,
                sensors_in_warning INTEGER DEFAULT 0,
                sensors_in_critical INTEGER DEFAULT 0,
                fleet_status TEXT NOT NULL,
                correlation_detected BOOLEAN DEFAULT FALSE,
                notes TEXT,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_readings_sensor ON sensor_readings(sensor_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_readings_timestamp ON sensor_readings(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sketches_sensor ON sketches(sensor_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sketches_zone ON sketches(zone)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_sensor ON alerts(sensor_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp)")

        # Multi-metric telemetry (asset + metric per row)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                point_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                metric_id TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                quality TEXT,
                timestamp TEXT NOT NULL,
                delta_percent REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_sketches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                point_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                metric_id TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                zone TEXT NOT NULL,
                sketch TEXT NOT NULL,
                trend TEXT,
                window_avg REAL,
                window_min REAL,
                window_max REAL,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                point_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                metric_id TEXT NOT NULL,
                value REAL NOT NULL,
                unit TEXT,
                zone TEXT NOT NULL,
                severity TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                description TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                acknowledged BOOLEAN DEFAULT FALSE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS telemetry_fleet_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                active_points INTEGER NOT NULL,
                points_in_warning INTEGER DEFAULT 0,
                points_in_critical INTEGER DEFAULT 0,
                assets_in_warning INTEGER DEFAULT 0,
                assets_in_critical INTEGER DEFAULT 0,
                fleet_status TEXT NOT NULL,
                correlation_detected BOOLEAN DEFAULT FALSE,
                notes TEXT,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_readings_point "
            "ON telemetry_readings(point_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_readings_asset_metric "
            "ON telemetry_readings(asset_id, metric_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_readings_ts "
            "ON telemetry_readings(timestamp)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_sketches_point "
            "ON telemetry_sketches(point_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_telemetry_alerts_point "
            "ON telemetry_alerts(point_id)"
        )
        
        conn.commit()
        print(f"[sensor_db] Database initialized at {get_db_path()}")


# ─────────────────────────────────────────────────────────────────────────────
# WRITE OPERATIONS (called by the pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def insert_reading(
    sensor_id: str,
    temperature: float,
    timestamp: str,
    delta_percent: float = None,
    *,
    point_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    metric_id: Optional[str] = None,
    value: Optional[float] = None,
    unit: Optional[str] = None,
    quality: Optional[str] = None,
):
    """Insert a sensor reading that passed deadband (legacy + optional telemetry row)."""
    scalar = float(value if value is not None else temperature)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sensor_readings (sensor_id, temperature, timestamp, delta_percent) VALUES (?, ?, ?, ?)",
            (sensor_id, scalar, timestamp, delta_percent),
        )
        if TELEMETRY_DUAL_WRITE:
            pid, aid, mid, val = _resolve_identity(
                sensor_id, scalar, point_id=point_id, asset_id=asset_id, metric_id=metric_id
            )
            conn.execute(
                """INSERT INTO telemetry_readings
                   (point_id, asset_id, metric_id, value, unit, quality, timestamp, delta_percent)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    aid,
                    mid,
                    val,
                    unit or _metric_unit(mid),
                    quality,
                    timestamp,
                    delta_percent,
                ),
            )


def insert_sketch(
    sensor_id: str,
    temperature: float,
    zone: str,
    sketch: str,
    timestamp: str,
    trend: str = None,
    window_avg: float = None,
    window_min: float = None,
    window_max: float = None,
    *,
    point_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    metric_id: Optional[str] = None,
    value: Optional[float] = None,
    unit: Optional[str] = None,
):
    """Insert a sketch summary (legacy + optional telemetry row)."""
    scalar = float(value if value is not None else temperature)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sketches 
               (sensor_id, temperature, zone, sketch, trend, window_avg, window_min, window_max, timestamp) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sensor_id, scalar, zone, sketch, trend, window_avg, window_min, window_max, timestamp),
        )
        if TELEMETRY_DUAL_WRITE:
            pid, aid, mid, val = _resolve_identity(
                sensor_id, scalar, point_id=point_id, asset_id=asset_id, metric_id=metric_id
            )
            conn.execute(
                """INSERT INTO telemetry_sketches
                   (point_id, asset_id, metric_id, value, unit, zone, sketch, trend,
                    window_avg, window_min, window_max, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    aid,
                    mid,
                    val,
                    unit or _metric_unit(mid),
                    zone,
                    sketch,
                    trend,
                    window_avg,
                    window_min,
                    window_max,
                    timestamp,
                ),
            )


def insert_sketch_batch(rows: List[Dict[str, Any]]):
    """Insert multiple sketch summaries in a single transaction."""
    if not rows:
        return

    legacy_values = []
    telemetry_values = []

    for row in rows:
        sensor_id = row["sensor_id"]
        scalar = float(row.get("value", row["temperature"]))
        legacy_values.append(
            (
                sensor_id,
                scalar,
                row["zone"],
                row["sketch"],
                row.get("trend"),
                row.get("window_avg"),
                row.get("window_min"),
                row.get("window_max"),
                row["timestamp"],
            )
        )
        if TELEMETRY_DUAL_WRITE:
            pid, aid, mid, val = _resolve_identity(
                sensor_id,
                scalar,
                point_id=row.get("point_id"),
                asset_id=row.get("asset_id"),
                metric_id=row.get("metric_id"),
            )
            telemetry_values.append(
                (
                    pid,
                    aid,
                    mid,
                    val,
                    row.get("unit") or _metric_unit(mid),
                    row["zone"],
                    row["sketch"],
                    row.get("trend"),
                    row.get("window_avg"),
                    row.get("window_min"),
                    row.get("window_max"),
                    row["timestamp"],
                )
            )

    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO sketches
               (sensor_id, temperature, zone, sketch, trend, window_avg, window_min, window_max, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            legacy_values,
        )
        if telemetry_values:
            conn.executemany(
                """INSERT INTO telemetry_sketches
                   (point_id, asset_id, metric_id, value, unit, zone, sketch, trend,
                    window_avg, window_min, window_max, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                telemetry_values,
            )


def insert_alert(
    sensor_id: str,
    temperature: float,
    zone: str,
    severity: str,
    alert_type: str,
    description: str,
    timestamp: str,
    *,
    point_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    metric_id: Optional[str] = None,
    value: Optional[float] = None,
    unit: Optional[str] = None,
):
    """Insert an alert/anomaly (legacy + optional telemetry row)."""
    scalar = float(value if value is not None else temperature)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO alerts 
               (sensor_id, temperature, zone, severity, alert_type, description, timestamp) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sensor_id, scalar, zone, severity, alert_type, description, timestamp),
        )
        if TELEMETRY_DUAL_WRITE:
            pid, aid, mid, val = _resolve_identity(
                sensor_id, scalar, point_id=point_id, asset_id=asset_id, metric_id=metric_id
            )
            conn.execute(
                """INSERT INTO telemetry_alerts
                   (point_id, asset_id, metric_id, value, unit, zone, severity, alert_type, description, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    aid,
                    mid,
                    val,
                    unit or _metric_unit(mid),
                    zone,
                    severity,
                    alert_type,
                    description,
                    timestamp,
                ),
            )


def insert_fleet_status(
    active_sensors: int,
    sensors_in_warning: int,
    sensors_in_critical: int,
    fleet_status: str,
    timestamp: str,
    correlation_detected: bool = False,
    notes: str = None,
    *,
    assets_in_warning: int = 0,
    assets_in_critical: int = 0,
):
    """Insert a fleet status snapshot (legacy + optional telemetry row)."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO fleet_status 
               (active_sensors, sensors_in_warning, sensors_in_critical, fleet_status, correlation_detected, notes, timestamp) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                active_sensors,
                sensors_in_warning,
                sensors_in_critical,
                fleet_status,
                correlation_detected,
                notes,
                timestamp,
            ),
        )
        if TELEMETRY_DUAL_WRITE:
            conn.execute(
                """INSERT INTO telemetry_fleet_status
                   (active_points, points_in_warning, points_in_critical,
                    assets_in_warning, assets_in_critical, fleet_status,
                    correlation_detected, notes, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    active_sensors,
                    sensors_in_warning,
                    sensors_in_critical,
                    assets_in_warning,
                    assets_in_critical,
                    fleet_status,
                    correlation_detected,
                    notes,
                    timestamp,
                ),
            )


# ─────────────────────────────────────────────────────────────────────────────
# READ OPERATIONS (called by SAM agents via tools)
# ─────────────────────────────────────────────────────────────────────────────


def _use_telemetry_reads(conn: sqlite3.Connection) -> bool:
    """Prefer telemetry_* tables when enabled and any telemetry table has rows."""
    if not FLEET_QUERY_USE_TELEMETRY or not TELEMETRY_DUAL_WRITE:
        return False
    for table in (
        "telemetry_readings",
        "telemetry_sketches",
        "telemetry_alerts",
        "telemetry_fleet_status",
    ):
        try:
            if conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0:
                return True
        except sqlite3.OperationalError:
            continue
    return False


def _compat_observation_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add legacy sensor_id/temperature aliases for agent tools."""
    out = dict(row)
    out.setdefault("sensor_id", out.get("point_id"))
    out.setdefault("temperature", out.get("value"))
    return out


def _compat_fleet_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    out.setdefault("active_sensors", out.get("active_points"))
    out.setdefault("sensors_in_warning", out.get("points_in_warning"))
    out.setdefault("sensors_in_critical", out.get("points_in_critical"))
    return out


def _demo_asset_metric_from_probe(probe: str) -> Tuple[Optional[str], Optional[str]]:
    """Map demo probe ids (m1-humidity, m2-temp-motor, …) to asset + metric."""
    p = (probe or "").lower()
    asset_map = {"m1": "machine-001", "m2": "machine-002", "m3": "machine-003"}
    asset = None
    for prefix, machine in asset_map.items():
        if p.startswith(prefix):
            asset = machine
            break
    if not asset:
        return None, None
    if "humidity" in p:
        return asset, "humidity_rh"
    if "vibration" in p:
        return asset, "motor_vibration_mm_s"
    if "temp-inlet" in p or p.endswith("-inlet"):
        return asset, "inlet_temp_c"
    if "temp-outlet" in p or p.endswith("-outlet"):
        return asset, "outlet_temp_c"
    if "temp-motor" in p or p.endswith("-motor"):
        return asset, "motor_temp_c"
    return asset, None


def _point_match_clause(
    identifier: str,
    metric_id: Optional[str] = None,
    *,
    legacy_sensor_col: str = "sensor_id",
    telemetry: bool = False,
) -> Tuple[str, List[Any]]:
    """Match probe id, point_id, or asset (+ optional metric)."""
    ident = (identifier or "").strip()
    clauses: List[str] = []
    params: List[Any] = []

    if telemetry:
        clauses.append("point_id = ?")
        params.append(ident)
        clauses.append("point_id LIKE ?")
        params.append(f"%{ident}%")
        if ":" in ident:
            asset, metric = ident.split(":", 1)
            clauses.append("(asset_id = ? AND metric_id = ?)")
            params.extend([asset, metric])
        demo_asset, demo_metric = _demo_asset_metric_from_probe(ident)
        if demo_asset and demo_metric:
            clauses.append("(asset_id = ? AND metric_id = ?)")
            params.extend([demo_asset, demo_metric])
        pid, aid, mid, _ = _resolve_identity(ident, 0.0)
        if pid and pid != ident:
            clauses.append("point_id = ?")
            params.append(pid)
        if aid and mid:
            clauses.append("(asset_id = ? AND metric_id = ?)")
            params.extend([aid, mid])
        if metric_id:
            clauses.append("(asset_id = ? AND metric_id = ?)")
            params.extend([ident.split(":")[0] if ":" in ident else ident, metric_id])
    else:
        clauses.append(f"{legacy_sensor_col} = ?")
        params.append(ident)
        clauses.append(f"{legacy_sensor_col} LIKE ?")
        params.append(f"%{ident}%")

    return f"({' OR '.join(clauses)})", params


def get_recent_alerts(
    minutes: int = 60,
    severity: str = None,
    limit: int = 50,
    metric_id: str = None,
) -> List[Dict]:
    """Get recent alerts, optionally filtered by severity and metric."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        use_telemetry = _use_telemetry_reads(conn)
        table = "telemetry_alerts" if use_telemetry else "alerts"

        query = f"SELECT * FROM {table} WHERE timestamp > ?"
        params: List[Any] = [cutoff]

        if severity:
            query += " AND severity = ?"
            params.append(severity.upper())
        if metric_id and use_telemetry:
            query += " AND metric_id = ?"
            params.append(metric_id)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        out = [dict(row) for row in rows]
        if use_telemetry:
            out = [_compat_observation_row(r) for r in out]
        return out


def get_recent_sketches(
    minutes: int = 60,
    sensor_id: str = None,
    zone: str = None,
    limit: int = 100,
    metric_id: str = None,
) -> List[Dict]:
    """Get recent sketches, optionally filtered by point/sensor and metric."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        use_telemetry = _use_telemetry_reads(conn)
        table = "telemetry_sketches" if use_telemetry else "sketches"
        id_col = "point_id" if use_telemetry else "sensor_id"

        query = f"SELECT * FROM {table} WHERE timestamp > ?"
        params: List[Any] = [cutoff]

        if sensor_id:
            clause, clause_params = _point_match_clause(
                sensor_id, metric_id, telemetry=use_telemetry
            )
            query += f" AND {clause}"
            params.extend(clause_params)
        elif metric_id and use_telemetry:
            query += " AND metric_id = ?"
            params.append(metric_id)

        if zone:
            query += " AND zone = ?"
            params.append(zone.upper())

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        out = [dict(row) for row in rows]
        if use_telemetry:
            out = [_compat_observation_row(r) for r in out]
        return out


def get_sketches_since_days(
    days: int = 3,
    sensor_ids: Optional[List[str]] = None,
    limit: int = 100_000,
) -> List[Dict]:
    """
    Sketches in the last ``days`` days, optionally restricted to probe/point ids.
    Ordered oldest-first for timeline audits.
    """
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        use_telemetry = _use_telemetry_reads(conn)
        table = "telemetry_sketches" if use_telemetry else "sketches"
        id_col = "point_id" if use_telemetry else "sensor_id"

        if sensor_ids is not None and len(sensor_ids) > 0:
            placeholders = ",".join("?" * len(sensor_ids))
            query = (
                f"SELECT * FROM {table} WHERE timestamp > ? AND {id_col} IN ({placeholders}) "
                "ORDER BY timestamp ASC LIMIT ?"
            )
            params: List[Any] = [cutoff, *sensor_ids, limit]
        else:
            query = f"SELECT * FROM {table} WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?"
            params = [cutoff, limit]

        rows = conn.execute(query, params).fetchall()
        out = [dict(row) for row in rows]
        if use_telemetry:
            out = [_compat_observation_row(r) for r in out]
        return out


def get_sensor_history(
    sensor_id: str,
    minutes: int = 30,
    limit: int = 50,
    metric_id: str = None,
) -> List[Dict]:
    """Get recent readings for a probe id / point (optional metric filter)."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        use_telemetry = _use_telemetry_reads(conn)

        if use_telemetry:
            clause, clause_params = _point_match_clause(sensor_id, metric_id, telemetry=True)
            rows = conn.execute(
                f"""SELECT * FROM telemetry_readings
                    WHERE timestamp > ? AND {clause}
                    ORDER BY timestamp DESC LIMIT ?""",
                [cutoff, *clause_params, limit],
            ).fetchall()
            return [_compat_observation_row(dict(row)) for row in rows]

        rows = conn.execute(
            """SELECT * FROM sensor_readings
               WHERE sensor_id = ? AND timestamp > ?
               ORDER BY timestamp DESC LIMIT ?""",
            (sensor_id, cutoff, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_fleet_status_history(minutes: int = 60, limit: int = 20) -> List[Dict]:
    """Get recent fleet status snapshots."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        use_telemetry = _use_telemetry_reads(conn)
        table = "telemetry_fleet_status" if use_telemetry else "fleet_status"

        rows = conn.execute(
            f"""SELECT * FROM {table}
                WHERE timestamp > ?
                ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()

        out = [dict(row) for row in rows]
        if use_telemetry:
            out = [_compat_fleet_row(r) for r in out]
        return out


def get_alert_summary(minutes: int = 60) -> Dict:
    """Get a summary of alerts in the time window."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        use_telemetry = _use_telemetry_reads(conn)
        table = "telemetry_alerts" if use_telemetry else "alerts"
        id_col = "point_id" if use_telemetry else "sensor_id"

        severity_counts = {}
        for sev in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]:
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE timestamp > ? AND severity = ?",
                (cutoff, sev),
            ).fetchone()[0]
            severity_counts[sev.lower()] = count

        sensor_counts = conn.execute(
            f"""SELECT {id_col} as sensor_id, COUNT(*) as count
                FROM {table} WHERE timestamp > ?
                GROUP BY {id_col} ORDER BY count DESC""",
            (cutoff,),
        ).fetchall()

        latest = conn.execute(
            f"SELECT * FROM {table} WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 1",
            (cutoff,),
        ).fetchone()

        latest_out = dict(latest) if latest else None
        if latest_out and use_telemetry:
            latest_out = _compat_observation_row(latest_out)

        summary = {
            "time_window_minutes": minutes,
            "total_alerts": sum(severity_counts.values()),
            "by_severity": severity_counts,
            "by_sensor": {row["sensor_id"]: row["count"] for row in sensor_counts},
            "latest_alert": latest_out,
            "data_source": "telemetry_alerts" if use_telemetry else "alerts",
        }
        if use_telemetry:
            metric_counts = conn.execute(
                f"""SELECT metric_id, COUNT(*) as count FROM {table}
                    WHERE timestamp > ? GROUP BY metric_id ORDER BY count DESC""",
                (cutoff,),
            ).fetchall()
            summary["by_metric"] = {row["metric_id"]: row["count"] for row in metric_counts}
        return summary


def get_current_fleet_status() -> Dict:
    """Get the most recent fleet status."""
    with get_connection() as conn:
        use_telemetry = _use_telemetry_reads(conn)
        table = "telemetry_fleet_status" if use_telemetry else "fleet_status"
        row = conn.execute(f"SELECT * FROM {table} ORDER BY timestamp DESC LIMIT 1").fetchone()

        if row:
            out = dict(row)
            if use_telemetry:
                out = _compat_fleet_row(out)
                out["data_source"] = "telemetry_fleet_status"
            return out
        return {
            "fleet_status": "UNKNOWN",
            "active_sensors": 0,
            "sensors_in_warning": 0,
            "sensors_in_critical": 0,
            "notes": "No fleet status data available yet",
        }


def list_active_metrics(minutes: int = 60) -> List[Dict[str, Any]]:
    """Distinct metrics seen in telemetry_readings (for agent discovery)."""
    with get_connection() as conn:
        if not _use_telemetry_reads(conn):
            return []
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        rows = conn.execute(
            """SELECT metric_id, unit, COUNT(*) as reading_count,
                      COUNT(DISTINCT asset_id) as asset_count
               FROM telemetry_readings
               WHERE timestamp > ?
               GROUP BY metric_id, unit
               ORDER BY metric_id""",
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_statistics() -> Dict:
    """Get overall database statistics."""
    with get_connection() as conn:
        readings_count = conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0]
        sketches_count = conn.execute("SELECT COUNT(*) FROM sketches").fetchone()[0]
        alerts_count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]

        sensors = conn.execute("SELECT DISTINCT sensor_id FROM sensor_readings").fetchall()

        stats = {
            "total_readings": readings_count,
            "total_sketches": sketches_count,
            "total_alerts": alerts_count,
            "active_sensors": [row["sensor_id"] for row in sensors],
            "database_path": get_db_path(),
            "telemetry_dual_write": TELEMETRY_DUAL_WRITE,
        }

        stats["fleet_query_reads_telemetry"] = False
        if _use_telemetry_reads(conn):
            stats["fleet_query_reads_telemetry"] = True
            stats["data_source_reads"] = "telemetry_*"

        if TELEMETRY_DUAL_WRITE:
            stats["telemetry_readings"] = conn.execute(
                "SELECT COUNT(*) FROM telemetry_readings"
            ).fetchone()[0]
            stats["telemetry_sketches"] = conn.execute(
                "SELECT COUNT(*) FROM telemetry_sketches"
            ).fetchone()[0]
            stats["telemetry_alerts"] = conn.execute(
                "SELECT COUNT(*) FROM telemetry_alerts"
            ).fetchone()[0]
            points = conn.execute(
                "SELECT DISTINCT point_id FROM telemetry_readings"
            ).fetchall()
            stats["active_points"] = [row["point_id"] for row in points]

        return stats


# Initialize on import
if __name__ == "__main__":
    init_database()
    print(get_statistics())
