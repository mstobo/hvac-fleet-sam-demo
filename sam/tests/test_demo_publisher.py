#!/usr/bin/env python3
"""Unit tests for demo_publisher payload builders (no MQTT)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pipeline_config as config  # noqa: E402
import demo_publisher as pub  # noqa: E402


class DemoPublisherTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None
        pub.DEMO_PUBLISH_MODE = "topics"

    def test_fifteen_points_total(self):
        self.assertEqual(len(pub.ALL_POINTS), 15)

    def test_build_payload_has_metric_and_value(self):
        point = pub.ALL_POINTS[0]
        payload = pub.build_payload(point, seq=1, spike=0.0)
        self.assertEqual(payload["metric"], point.metric_id)
        self.assertIn("value", payload)
        self.assertEqual(payload["pointId"], config.make_point_id(point.machine_id, point.metric_id))

    def test_bundle_payload_readings_count(self):
        machine = pub.MACHINES[0]
        bundle = pub.build_bundle_payload(machine, seq=1, spikes={})
        self.assertEqual(bundle["schema"], config.SCHEMA_RAW_BUNDLE)
        self.assertEqual(len(bundle["readings"]), 5)
        metrics = {r["metric"] for r in bundle["readings"]}
        self.assertIn("humidity_rh", metrics)
        self.assertIn("motor_vibration_mm_s", metrics)

    def test_topic_per_metric(self):
        point = next(p for p in pub.ALL_POINTS if p.metric_id == "humidity_rh")
        self.assertTrue(pub.topic_for_point(point).endswith("/humidity_rh"))

    def test_bundle_topic_suffix(self):
        self.assertTrue(
            pub.bundle_topic_for_machine("machine-001").endswith(f"/{config.BUNDLE_TOPIC_METRIC}")
        )

    def test_hvac_scenario_includes_humidity(self):
        hvac = next(s for s in pub.SCENARIOS if s.name == "HVAC Failure")
        self.assertIn("m1-humidity", hvac.affected_points)


if __name__ == "__main__":
    unittest.main()
