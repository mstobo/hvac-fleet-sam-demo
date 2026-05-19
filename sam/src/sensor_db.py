#!/usr/bin/env python3
"""
sensor_db.py
============
SQLite database for sensor pipeline data.
Acts as the "time-series DB" for the demo.

Tables:
  - sensor_readings: Raw readings that passed deadband
  - sketches: Natural language summaries with zone classification
  - alerts: Detected anomalies and warnings
  - fleet_status: Periodic fleet health snapshots
"""

import sqlite3
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

import pipeline_config as _config

log = _config.get_logger("sensor_db")


def _cutoff_iso(seconds_ago: float = 0.0, *, minutes: float = 0.0, days: float = 0.0) -> str:
    """
    Return an ISO timestamp `now - delta` in *naive* form (no tz suffix). Matches the
    format we get from upstream services that write `<iso>Z` strings — naive-vs-Z compares
    correctly for "newer than cutoff" queries since Z > '' alphabetically.
    """
    delta = timedelta(seconds=seconds_ago, minutes=minutes, days=days)
    return (datetime.now(timezone.utc) - delta).replace(tzinfo=None).isoformat()

# Database file location. Override via SENSOR_DB_PATH env var (parity with chart_db).
DB_PATH = os.getenv(
    "SENSOR_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "sensor_data.db"),
)


def get_db_path() -> str:
    """Get the absolute path to the database file."""
    return os.path.abspath(DB_PATH)


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    # journal_mode=WAL is persisted in the file header by init_database(); synchronous is per-connection
    # so it must be set on every open. NORMAL is the SQLite-recommended pair for WAL.
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_database():
    """Initialize the database schema."""
    with get_connection() as conn:
        # WAL mode is persisted in the file header (one-time switch). synchronous=NORMAL is per-connection
        # and is applied in get_connection() above; setting it here is a no-op on this connection but
        # keeps the intent visible at init time.
        conn.execute("PRAGMA journal_mode=WAL")

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
        
        conn.commit()
        log.info("database initialized at %s", get_db_path())


# ─────────────────────────────────────────────────────────────────────────────
# WRITE OPERATIONS (called by the pipeline)
# ─────────────────────────────────────────────────────────────────────────────

def insert_reading(sensor_id: str, temperature: float, timestamp: str, delta_percent: float = None):
    """Insert a sensor reading that passed deadband."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sensor_readings (sensor_id, temperature, timestamp, delta_percent) VALUES (?, ?, ?, ?)",
            (sensor_id, temperature, timestamp, delta_percent)
        )


def insert_sketch(sensor_id: str, temperature: float, zone: str, sketch: str, 
                  timestamp: str, trend: str = None, window_avg: float = None,
                  window_min: float = None, window_max: float = None):
    """Insert a sketch summary."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO sketches 
               (sensor_id, temperature, zone, sketch, trend, window_avg, window_min, window_max, timestamp) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sensor_id, temperature, zone, sketch, trend, window_avg, window_min, window_max, timestamp)
        )


def insert_sketch_batch(rows: List[Dict[str, Any]]):
    """Insert multiple sketch summaries in a single transaction."""
    if not rows:
        return

    values = [
        (
            row["sensor_id"],
            row["temperature"],
            row["zone"],
            row["sketch"],
            row.get("trend"),
            row.get("window_avg"),
            row.get("window_min"),
            row.get("window_max"),
            row["timestamp"],
        )
        for row in rows
    ]

    with get_connection() as conn:
        conn.executemany(
            """INSERT INTO sketches
               (sensor_id, temperature, zone, sketch, trend, window_avg, window_min, window_max, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )


def insert_alert(sensor_id: str, temperature: float, zone: str, severity: str,
                 alert_type: str, description: str, timestamp: str):
    """Insert an alert/anomaly."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO alerts 
               (sensor_id, temperature, zone, severity, alert_type, description, timestamp) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sensor_id, temperature, zone, severity, alert_type, description, timestamp)
        )


def insert_fleet_status(active_sensors: int, sensors_in_warning: int, sensors_in_critical: int,
                        fleet_status: str, timestamp: str, correlation_detected: bool = False, notes: str = None):
    """Insert a fleet status snapshot."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO fleet_status 
               (active_sensors, sensors_in_warning, sensors_in_critical, fleet_status, correlation_detected, notes, timestamp) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (active_sensors, sensors_in_warning, sensors_in_critical, fleet_status, correlation_detected, notes, timestamp)
        )


# ─────────────────────────────────────────────────────────────────────────────
# READ OPERATIONS (called by SAM agents via tools)
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_alerts(minutes: int = 60, severity: str = None, limit: int = 50) -> List[Dict]:
    """Get recent alerts, optionally filtered by severity."""
    with get_connection() as conn:
        cutoff = _cutoff_iso(minutes=minutes)
        
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
        cutoff = _cutoff_iso(minutes=minutes)
        
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
        cutoff = _cutoff_iso(days=days)
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
        cutoff = _cutoff_iso(minutes=minutes)
        
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
        cutoff = _cutoff_iso(minutes=minutes)
        
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
        cutoff = _cutoff_iso(minutes=minutes)
        
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
        
        # Distinct sensors
        sensors = conn.execute("SELECT DISTINCT sensor_id FROM sensor_readings").fetchall()
        
        return {
            "total_readings": readings_count,
            "total_sketches": sketches_count,
            "total_alerts": alerts_count,
            "active_sensors": [row['sensor_id'] for row in sensors],
            "database_path": get_db_path()
        }


# Initialize on import
if __name__ == "__main__":
    init_database()
    print(get_statistics())
