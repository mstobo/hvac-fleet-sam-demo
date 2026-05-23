"""
End-to-end test for the deterministic data plane: deadband → sketch → anomaly.

This test exercises the pure-function logic of each stage with synthetic readings
designed to trigger a CRITICAL alert. The MQTT layer is intentionally not in
scope — Paho is well-tested upstream, and the interesting branching lives in
the deterministic Python code.

What it proves:
  - deadband suppresses sub-threshold changes and forwards the first reading.
  - deadband classifies zone correctly when a reading crosses CRITICAL_TEMP.
  - sketch generates a narrative that flags the anomaly.
  - anomaly produces an alert with HIGH or CRITICAL severity for a real spike.
  - sketch + alert rows land in the tmp SQLite database.
"""

from __future__ import annotations


def test_critical_spike_flows_through_pipeline(tmp_sensor_db, reset_pipeline_state):
    import pipeline_config as config
    import deadband_service
    import sketch_service
    import anomaly_service

    sensor_id = "test-motor-1"

    # 1. First reading — always forwards (no prior value to compare against).
    action, baseline = deadband_service.apply_deadband(
        sensor_id, 45.0, "2026-01-01T00:00:00Z"
    )
    assert action == "forward"
    assert baseline["zone"] == "NORMAL"
    assert baseline["forwarded_reason"] == "first-reading"

    # 2. Quiet reading just above noise floor — must be suppressed under deadband.
    action, _ = deadband_service.apply_deadband(
        sensor_id, 45.1, "2026-01-01T00:00:10Z"
    )
    assert action == "suppress"

    # 3. Spike into CRITICAL zone (config.CRITICAL_TEMP is 65.0°C).
    spike_temp = 72.0
    action, spike = deadband_service.apply_deadband(
        sensor_id, spike_temp, "2026-01-01T00:00:20Z"
    )
    assert action == "forward"
    assert spike["zone"] == "CRITICAL"
    assert spike["delta_pct"] > config.DEADBAND_PCT
    # deadband doesn't populate site/room — sketch/anomaly read them with .get fallbacks
    # but we set them explicitly so the test reflects what on_message does upstream.
    spike["site"] = config.DEFAULT_SITE
    spike["room"] = config.DEFAULT_ROOM
    spike["sensorId"] = sensor_id

    # 4. Sketch generation should narrate the spike, classify zone, and buffer to DB.
    sketch = sketch_service.generate_sketch(spike)
    assert sketch["zone"] == "CRITICAL"
    assert sketch["zone"] == "CRITICAL"
    assert ("Anomaly detected" in sketch["sketch"]) or ("!CRIT" in sketch["sketch"])
    # Force the per-batch DB flush so we can assert persistence below.
    sketch_service._flush_sketch_buffer(force=True)

    # 5. Anomaly stage produces an alert at the right severity.
    alert = anomaly_service.generate_alert(sketch)
    assert alert is not None, "expected an alert for a CRITICAL spike"
    assert alert["severity"] in {"HIGH", "CRITICAL"}, alert["severity"]
    assert alert["sensorId"] == sensor_id
    assert alert["zone"] == "CRITICAL"
    assert alert["alert_type"] in {"SPIKE", "THRESHOLD_BREACH"}

    # 6. Persistence — the sketch and alert should both be queryable from the tmp DB.
    sketches = tmp_sensor_db.get_recent_sketches(minutes=60)
    assert any(
        s["sensor_id"] == sensor_id and s["zone"] == "CRITICAL"
        for s in sketches
    ), "sketch row not found in tmp sensor_db"

    alerts = tmp_sensor_db.get_recent_alerts(minutes=60)
    assert any(
        a["sensor_id"] == sensor_id and a["severity"] in {"HIGH", "CRITICAL"}
        for a in alerts
    ), "alert row not found in tmp sensor_db"
