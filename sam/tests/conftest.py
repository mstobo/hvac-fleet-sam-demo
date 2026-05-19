"""
Shared pytest fixtures for sam/tests/.

Pipeline services (deadband, sketch, anomaly) keep module-level state — last
values, rolling windows, sketch buffers, fleet zone maps. These fixtures wipe
that state between tests so each test starts from a known baseline.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# Ensure the production source is on the import path. Tests live in sam/tests/,
# services live in sam/src/.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture
def tmp_sensor_db(tmp_path, monkeypatch):
    """
    Redirect sensor_db to a fresh SQLite file in tmp_path and initialise the schema.
    Yields the reloaded sensor_db module (already pointing at the tmp file).
    """
    db_path = tmp_path / "sensor_data.db"
    monkeypatch.setenv("SENSOR_DB_PATH", str(db_path))

    import sensor_db  # noqa: WPS433 — production module, imported here for reload
    importlib.reload(sensor_db)
    sensor_db.init_database()
    yield sensor_db


@pytest.fixture
def reset_pipeline_state(monkeypatch):
    """
    Reset the module-level state that deadband, sketch, and anomaly carry across
    messages. Also disable optional side effects (Slack, fleet auto-analysis) so
    tests don't reach the network.
    """
    import deadband_service
    import sketch_service
    import anomaly_service

    deadband_service._last_value.clear()
    deadband_service._last_forward_ts.clear()
    deadband_service._windows.clear()

    sketch_service._sketch_buffer.clear()
    sketch_service._processed_count = 0

    anomaly_service._sensor_zones.clear()
    monkeypatch.setattr(anomaly_service, "SLACK_ENABLED", False)
    monkeypatch.setattr(anomaly_service, "AUTO_ANALYSIS_ENABLED", False)

    yield
