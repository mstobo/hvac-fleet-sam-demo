#!/usr/bin/env python3
"""Unit tests for multi-metric telemetry helpers in pipeline_config."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pipeline_config as config  # noqa: E402


class ResolvePointIdTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None

    def test_asset_and_metric_from_payload(self):
        pid, asset, metric = config.resolve_point_id(
            {"asset": "crac-07", "metric": "humidity_rh"},
            {"site": "dc1"},
        )
        self.assertEqual(asset, "crac-07")
        self.assertEqual(metric, "humidity_rh")
        self.assertEqual(pid, "crac-07:humidity_rh")

    def test_explicit_point_id(self):
        pid, asset, metric = config.resolve_point_id(
            {"pointId": "machine-001:supply_temp_c", "asset": "machine-001", "metric": "supply_temp_c"},
            {},
        )
        self.assertEqual(pid, "machine-001:supply_temp_c")
        self.assertEqual(asset, "machine-001")
        self.assertEqual(metric, "supply_temp_c")

    def test_legacy_sensor_id(self):
        pid, asset, metric = config.resolve_point_id(
            {"sensorId": "m1-temp-inlet", "machineId": "machine-001", "temperature": 38.2},
            {},
        )
        self.assertEqual(pid, "m1-temp-inlet")
        self.assertEqual(asset, "machine-001")
        self.assertEqual(metric, "supply_temp_c")

    def test_canonical_point_id_as_sensor_id(self):
        pid, asset, metric = config.resolve_point_id(
            {"sensorId": "machine-002:motor_temp_c"},
            {},
        )
        self.assertEqual(pid, "machine-002:motor_temp_c")
        self.assertEqual(asset, "machine-002")
        self.assertEqual(metric, "motor_temp_c")

    def test_topic_meta_asset_metric(self):
        pid, asset, metric = config.resolve_point_id(
            {},
            {"asset": "crac-07", "metric": "motor_vibration_mm_s"},
        )
        self.assertEqual(pid, "crac-07:motor_vibration_mm_s")

    def test_bundle_topic_returns_none(self):
        pid, asset, metric = config.resolve_point_id(
            {},
            {"asset": "crac-07", "metric": "_bundle"},
        )
        self.assertIsNone(pid)
        self.assertEqual(metric, "_bundle")

    def test_observation_value_prefers_value_over_temperature(self):
        self.assertEqual(
            config.observation_value({"value": 62.1, "temperature": 99.0}),
            62.1,
        )
        self.assertEqual(config.observation_value({"temperature": 38.0}), 38.0)
        self.assertEqual(config.observation_value({}, topic_value=41.5), 41.5)


class ClassifyZoneTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None

    def test_supply_temp_critical(self):
        self.assertEqual(config.classify_zone(66.0, "supply_temp_c"), "CRITICAL")

    def test_humidity_warning_not_temp_threshold(self):
        # Same numeric value, different rules per metric
        self.assertEqual(config.classify_zone(72.0, "humidity_rh"), "WARNING")
        self.assertEqual(config.classify_zone(72.0, "supply_temp_c"), "CRITICAL")

    def test_vibration_normal(self):
        self.assertEqual(config.classify_zone(0.5, "motor_vibration_mm_s"), "NORMAL")

    def test_default_metric_when_metric_omitted(self):
        self.assertEqual(config.classify_zone(66.0), "CRITICAL")


class BundlePayloadTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None

    def test_is_bundle_by_schema(self):
        self.assertTrue(
            config.is_bundle_payload(
                {"schema": config.SCHEMA_RAW_BUNDLE, "asset": "crac-07", "readings": []}
            )
        )

    def test_expand_bundle_three_points(self):
        payload = {
            "schema": config.SCHEMA_RAW_BUNDLE,
            "ts": "2026-05-21T12:00:00Z",
            "asset": "crac-07",
            "site": "dc1",
            "readings": [
                {"metric": "supply_temp_c", "value": 29.4, "unit": "C"},
                {"metric": "humidity_rh", "value": 62.1, "unit": "%RH"},
                {"metric": "motor_vibration_mm_s", "value": 0.82, "unit": "mm/s"},
            ],
        }
        expanded = config.expand_bundle_payload(payload)
        self.assertEqual(len(expanded), 3)
        metrics = {p["metric"] for p in expanded}
        self.assertEqual(
            metrics,
            {"supply_temp_c", "humidity_rh", "motor_vibration_mm_s"},
        )
        for point in expanded:
            self.assertEqual(point["asset"], "crac-07")
            self.assertEqual(point["pointId"], config.make_point_id("crac-07", point["metric"]))
            self.assertEqual(point["value"], point["temperature"])

    def test_per_metric_deadband_pct(self):
        self.assertEqual(config.deadband_pct_for("supply_temp_c"), 0.02)
        self.assertEqual(config.deadband_pct_for("humidity_rh"), 0.05)
        self.assertEqual(config.deadband_pct_for("motor_vibration_mm_s"), 0.1)


if __name__ == "__main__":
    unittest.main()
