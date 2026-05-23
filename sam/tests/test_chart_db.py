#!/usr/bin/env python3
"""Unit tests for chart_db multi-metric series identity and filtering."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chart_db  # noqa: E402
import pipeline_config as config  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ChartDbTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["CHART_DB_PATH"] = os.path.join(self._tmpdir.name, "chart_data.db")
        chart_db.DB_PATH = os.environ["CHART_DB_PATH"]
        chart_db.init_database()

    def tearDown(self):
        os.environ.pop("CHART_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_resolve_probe_humidity(self):
        identity = chart_db.resolve_chart_identity(sensor_id="m1-humidity")
        self.assertEqual(identity["point_id"], "machine-001:humidity_rh")
        self.assertEqual(identity["metric_id"], "humidity_rh")

    def test_resolve_canonical_sensor_id(self):
        identity = chart_db.resolve_chart_identity(sensor_id="machine-002:outlet_temp_c")
        self.assertEqual(identity["point_id"], "machine-002:outlet_temp_c")
        self.assertEqual(identity["asset_id"], "machine-002")
        self.assertEqual(identity["metric_id"], "outlet_temp_c")
        self.assertNotIn("supply_temp_c", identity["point_id"])

    def test_write_and_filter_by_metric(self):
        ts = _utc_now()
        chart_db.write_point_and_rollups(
            ts,
            sensor_id="machine-001:humidity_rh",
            value=55.0,
            source="filtered",
            metric_id="humidity_rh",
            asset_id="machine-001",
            unit="%RH",
        )
        chart_db.write_point_and_rollups(
            ts,
            sensor_id="machine-001:inlet_temp_c",
            value=40.0,
            source="filtered",
            metric_id="inlet_temp_c",
            asset_id="machine-001",
            unit="C",
        )

        clause, params = chart_db.build_series_filter_sql(
            "m1-humidity",
            include_metric_column=True,
        )
        with chart_db.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT sensor_id, metric_id, value FROM chart_points
                WHERE ts = ? {clause}
                """,
                [ts, *params],
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["metric_id"], "humidity_rh")

    def test_filter_asset_and_metric_params(self):
        ts = _utc_now()
        chart_db.write_point_and_rollups(
            ts,
            sensor_id="machine-002:motor_vibration_mm_s",
            value=0.8,
            source="filtered",
            asset_id="machine-002",
            metric_id="motor_vibration_mm_s",
        )
        clause, params = chart_db.build_series_filter_sql(
            "",
            metric_id="motor_vibration_mm_s",
            asset_id="machine-002",
            include_metric_column=True,
        )
        with chart_db.get_connection() as conn:
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM chart_points WHERE 1=1 {clause}",
                params,
            ).fetchone()["n"]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
