#!/usr/bin/env python3
"""Unit tests for telemetry_* tables and dual-write in sensor_db."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pipeline_config as config  # noqa: E402
import sensor_db  # noqa: E402


class SensorDbTelemetryTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._tmpdir.name, "test_sensor_data.db")
        os.environ["SENSOR_DB_PATH"] = self._db_path
        sensor_db.DB_PATH = self._db_path
        sensor_db.TELEMETRY_DUAL_WRITE = True
        sensor_db.init_database()

    def tearDown(self):
        os.environ.pop("SENSOR_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_insert_reading_dual_write(self):
        sensor_db.insert_reading(
            sensor_id="crac-07:humidity_rh",
            temperature=62.1,
            timestamp="2026-05-21T12:00:00Z",
            delta_percent=0.04,
            point_id="crac-07:humidity_rh",
            asset_id="crac-07",
            metric_id="humidity_rh",
            value=62.1,
            unit="%RH",
        )
        with sensor_db.get_connection() as conn:
            legacy = conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0]
            telem = conn.execute("SELECT COUNT(*) FROM telemetry_readings").fetchone()[0]
            row = conn.execute(
                "SELECT metric_id, value, unit FROM telemetry_readings LIMIT 1"
            ).fetchone()
        self.assertEqual(legacy, 1)
        self.assertEqual(telem, 1)
        self.assertEqual(row["metric_id"], "humidity_rh")
        self.assertEqual(row["value"], 62.1)
        self.assertEqual(row["unit"], "%RH")

    def test_insert_sketch_batch_dual_write(self):
        sensor_db.insert_sketch_batch(
            [
                {
                    "sensor_id": "crac-07:supply_temp_c",
                    "point_id": "crac-07:supply_temp_c",
                    "asset_id": "crac-07",
                    "metric_id": "supply_temp_c",
                    "value": 40.0,
                    "temperature": 40.0,
                    "zone": "NORMAL",
                    "sketch": "test sketch",
                    "timestamp": "2026-05-21T12:00:01Z",
                }
            ]
        )
        with sensor_db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sketches").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM telemetry_sketches").fetchone()[0], 1
            )

    def test_fields_from_pipeline_message(self):
        fields = sensor_db.fields_from_pipeline_message(
            {
                "pointId": "crac-07:motor_vibration_mm_s",
                "asset": "crac-07",
                "metric": "motor_vibration_mm_s",
                "value": 0.9,
                "unit": "mm/s",
                "sensorId": "crac-07:motor_vibration_mm_s",
            }
        )
        self.assertEqual(fields["point_id"], "crac-07:motor_vibration_mm_s")
        self.assertEqual(fields["metric_id"], "motor_vibration_mm_s")
        self.assertEqual(fields["value"], 0.9)

    def test_dual_write_disabled(self):
        sensor_db.TELEMETRY_DUAL_WRITE = False
        sensor_db.insert_reading(
            "legacy-only",
            1.0,
            "2026-05-21T12:00:00Z",
        )
        with sensor_db.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM telemetry_readings").fetchone()[0], 0
            )


if __name__ == "__main__":
    unittest.main()
