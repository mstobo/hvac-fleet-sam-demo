"""
Unit tests for the thread-local connection pool in sensor_db + chart_db.

These prove the change has the intended effect (same Connection object reused
across calls in the same thread) and behaves correctly on the rollback path.
"""

from __future__ import annotations

import sqlite3

import pytest


def test_sensor_db_reuses_same_connection(tmp_sensor_db):
    """Consecutive get_connection() calls in the same thread return the same object."""
    with tmp_sensor_db.get_connection() as conn1:
        first_id = id(conn1)
    with tmp_sensor_db.get_connection() as conn2:
        second_id = id(conn2)
    assert first_id == second_id, "thread-local pool should reuse the same Connection"


def test_sensor_db_close_thread_connection_resets_cache(tmp_sensor_db):
    """After close_thread_connection(), the next get_connection() opens a fresh one."""
    with tmp_sensor_db.get_connection() as conn1:
        first_id = id(conn1)
    tmp_sensor_db.close_thread_connection()
    with tmp_sensor_db.get_connection() as conn2:
        second_id = id(conn2)
    assert first_id != second_id, "after close, pool must create a fresh Connection"


def test_sensor_db_rolls_back_on_exception(tmp_sensor_db, reset_pipeline_state):
    """Uncommitted writes inside a failing with-block must NOT persist."""
    import pipeline_config as config
    # Use a current timestamp so get_sensor_history's "last N minutes" filter sees the row.
    ts = config.now_utc_iso()

    # Insert one row that COMMITS — establishes baseline state.
    tmp_sensor_db.insert_reading("rollback-sensor", 50.0, ts)

    # Now run a transaction that errors out mid-way. The connection is pooled,
    # so a failed rollback would leak phantom writes into the next call.
    with pytest.raises(RuntimeError):
        with tmp_sensor_db.get_connection() as conn:
            conn.execute(
                "INSERT INTO sensor_readings (sensor_id, temperature, timestamp) VALUES (?, ?, ?)",
                ("rollback-sensor", 99.9, ts),
            )
            raise RuntimeError("simulated mid-transaction failure")

    # Only the first (committed) row should be present; the second was rolled back.
    rows = tmp_sensor_db.get_sensor_history("rollback-sensor", minutes=60)
    temps = [r["temperature"] for r in rows]
    assert 50.0 in temps, f"committed row missing; rows={rows}"
    assert 99.9 not in temps, "rolled-back write should not be visible to subsequent queries"


def test_sensor_db_pool_survives_pragma_setting(tmp_sensor_db):
    """The synchronous pragma should be set exactly once per thread, on first connect.
    Verifies the pooled connection still has NORMAL synchronous (i.e. pragma persisted)."""
    with tmp_sensor_db.get_connection() as conn:
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
    # SQLite reports synchronous as: 0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA
    assert synchronous == 1, f"expected synchronous=NORMAL (1), got {synchronous}"
