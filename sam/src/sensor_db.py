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

def get_recent_alerts(minutes: int = 60, severity: str = None, limit: int = 50) -> List[Dict]:
    """Get recent alerts, optionally filtered by severity."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        
        if severity:
            rows = conn.execute(
                """SELECT * FROM alerts 
                   WHERE timestamp > ? AND severity = ? 
                   ORDER BY timestamp DESC LIMIT ?""",
                (cutoff, severity.upper(), limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM alerts 
                   WHERE timestamp > ? 
                   ORDER BY timestamp DESC LIMIT ?""",
                (cutoff, limit)
            ).fetchall()
        
        return [dict(row) for row in rows]


def get_recent_sketches(minutes: int = 60, sensor_id: str = None, zone: str = None, limit: int = 100) -> List[Dict]:
    """Get recent sketches, optionally filtered."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        
        query = "SELECT * FROM sketches WHERE timestamp > ?"
        params = [cutoff]
        
        if sensor_id:
            query += " AND sensor_id = ?"
            params.append(sensor_id)
        if zone:
            query += " AND zone = ?"
            params.append(zone.upper())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_sketches_since_days(
    days: int = 3,
    sensor_ids: Optional[List[str]] = None,
    limit: int = 100_000,
) -> List[Dict]:
    """
    Sketches in the last ``days`` days, optionally restricted to ``sensor_ids``.
    ``sensor_ids`` None or empty = all sensors. Ordered oldest-first for timeline audits.
    """
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        if sensor_ids is not None and len(sensor_ids) > 0:
            placeholders = ",".join("?" * len(sensor_ids))
            query = (
                f"SELECT * FROM sketches WHERE timestamp > ? AND sensor_id IN ({placeholders}) "
                "ORDER BY timestamp ASC LIMIT ?"
            )
            params: List[Any] = [cutoff, *sensor_ids, limit]
        else:
            query = "SELECT * FROM sketches WHERE timestamp > ? ORDER BY timestamp ASC LIMIT ?"
            params = [cutoff, limit]
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_sensor_history(sensor_id: str, minutes: int = 30, limit: int = 50) -> List[Dict]:
    """Get recent readings for a specific sensor."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        
        rows = conn.execute(
            """SELECT * FROM sensor_readings 
               WHERE sensor_id = ? AND timestamp > ? 
               ORDER BY timestamp DESC LIMIT ?""",
            (sensor_id, cutoff, limit)
        ).fetchall()
        
        return [dict(row) for row in rows]


def get_fleet_status_history(minutes: int = 60, limit: int = 20) -> List[Dict]:
    """Get recent fleet status snapshots."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        
        rows = conn.execute(
            """SELECT * FROM fleet_status 
               WHERE timestamp > ? 
               ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, limit)
        ).fetchall()
        
        return [dict(row) for row in rows]


def get_alert_summary(minutes: int = 60) -> Dict:
    """Get a summary of alerts in the time window."""
    with get_connection() as conn:
        cutoff = (datetime.utcnow() - timedelta(minutes=minutes)).isoformat()
        
        # Count by severity
        severity_counts = {}
        for sev in ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']:
            count = conn.execute(
                "SELECT COUNT(*) FROM alerts WHERE timestamp > ? AND severity = ?",
                (cutoff, sev)
            ).fetchone()[0]
            severity_counts[sev.lower()] = count
        
        # Count by sensor
        sensor_counts = conn.execute(
            """SELECT sensor_id, COUNT(*) as count 
               FROM alerts WHERE timestamp > ? 
               GROUP BY sensor_id ORDER BY count DESC""",
            (cutoff,)
        ).fetchall()
        
        # Most recent alert
        latest = conn.execute(
            "SELECT * FROM alerts WHERE timestamp > ? ORDER BY timestamp DESC LIMIT 1",
            (cutoff,)
        ).fetchone()
        
        return {
            "time_window_minutes": minutes,
            "total_alerts": sum(severity_counts.values()),
            "by_severity": severity_counts,
            "by_sensor": {row['sensor_id']: row['count'] for row in sensor_counts},
            "latest_alert": dict(latest) if latest else None
        }


def get_current_fleet_status() -> Dict:
    """Get the most recent fleet status."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM fleet_status ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        
        if row:
            return dict(row)
        return {
            "fleet_status": "UNKNOWN",
            "active_sensors": 0,
            "sensors_in_warning": 0,
            "sensors_in_critical": 0,
            "notes": "No fleet status data available yet"
        }


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
