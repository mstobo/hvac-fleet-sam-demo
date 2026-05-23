#!/usr/bin/env python3
"""Tests for combined machine-level Plotly charts."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chart_db  # noqa: E402
import chart_query_service as cqs  # noqa: E402
import fleet_query_tools as fqt  # noqa: E402
import pipeline_config as config  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class MachinePlotlyTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["CHART_DB_PATH"] = os.path.join(self._tmpdir.name, "chart_data.db")
        chart_db.DB_PATH = os.environ["CHART_DB_PATH"]
        chart_db.init_database()

        ts = _utc_now()
        for metric, base in (
            ("inlet_temp_c", 55.0),
            ("outlet_temp_c", 60.0),
            ("motor_temp_c", 70.0),
        ):
            point = config.make_point_id("machine-001", metric)
            chart_db.write_point_and_rollups(
                ts,
                point,
                base + 5.0,
                "filtered",
                zone="WARNING",
                asset_id="machine-001",
                metric_id=metric,
            )

    def tearDown(self):
        os.environ.pop("CHART_DB_PATH", None)
        self._tmpdir.cleanup()

    def test_build_machine_plotly_bundle_three_traces(self):
        bundle = cqs.build_machine_plotly_bundle(
            "machine-001",
            list(cqs.DEFAULT_MACHINE_TEMP_METRICS),
            minutes=120,
            value_key="avg_v",
        )
        spec = bundle["plotly_spec"]
        self.assertEqual(len(spec["data"]), 3)
        self.assertGreater(int(bundle["stats"]["rendered_row_count"]), 0)
        names = {t["name"] for t in spec["data"]}
        self.assertEqual(names, {"inlet", "outlet", "motor"})

    def test_build_machine_plotly_html_url(self):
        os.environ["CHART_PUBLIC_BASE_URL"] = "http://demo.example/charts"
        url = fqt.build_machine_plotly_html_url("machine-001", minutes=120)
        self.assertIn("/machine-plotly-html?", url or "")
        self.assertIn("asset_id=machine-001", url or "")
        self.assertIn("inlet_temp_c", url or "")
        os.environ.pop("CHART_PUBLIC_BASE_URL", None)

    def test_parse_machine_chart_params_defaults(self):
        parsed = cqs._parse_machine_chart_params({})
        self.assertIn("error", parsed)

        parsed = cqs._parse_machine_chart_params({"asset_id": ["machine-002"]})
        self.assertEqual(parsed["asset_id"], "machine-002")
        self.assertEqual(parsed["metrics"], list(cqs.DEFAULT_MACHINE_TEMP_METRICS))


if __name__ == "__main__":
    unittest.main()
