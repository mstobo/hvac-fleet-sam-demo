#!/usr/bin/env python3
"""Unit tests for deadband_service (per-point state and bundle fan-out)."""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pipeline_config as config  # noqa: E402
import deadband_service as deadband  # noqa: E402


class DeadbandServiceTests(unittest.TestCase):
    def setUp(self):
        config._metrics_config = None
        deadband.reset_deadband_state()

    def test_independent_state_per_metric_same_asset(self):
        client = MagicMock()
        base = {
            "asset": "crac-07",
            "site": "dc1",
            "ts": "2026-05-21T12:00:00Z",
        }
        # Establish baseline per point (first reading forwards)
        deadband.process_observation(
            client,
            {**base, "metric": "supply_temp_c", "value": 50.0},
        )
        deadband.process_observation(
            client,
            {**base, "metric": "humidity_rh", "value": 50.0},
        )
        client.reset_mock()

        # 0.2% change: below 2% temp deadband -> suppress
        deadband.process_observation(
            client,
            {**base, "metric": "supply_temp_c", "value": 50.1},
        )
        self.assertEqual(client.publish.call_count, 1)
        self.assertEqual(client.publish.call_args[0][0], config.TOPIC_SUPPRESSED)

        client.reset_mock()
        # 5.2% change: at humidity 5% deadband -> forward
        deadband.process_observation(
            client,
            {**base, "metric": "humidity_rh", "value": 52.6},
        )
        self.assertEqual(client.publish.call_count, 1)
        self.assertEqual(client.publish.call_args[0][0], config.TOPIC_FILTERED)

    def test_forward_payload_includes_point_id_and_value(self):
        client = MagicMock()
        deadband.process_observation(
            client,
            {
                "asset": "crac-07",
                "metric": "humidity_rh",
                "value": 55.0,
                "ts": "2026-05-21T12:00:00Z",
            },
        )
        self.assertTrue(client.publish.called)
        topic, body = client.publish.call_args[0]
        self.assertEqual(topic, config.TOPIC_FILTERED)
        msg = json.loads(body)
        self.assertEqual(msg["pointId"], "crac-07:humidity_rh")
        self.assertEqual(msg["value"], 55.0)
        self.assertEqual(msg["temperature"], 55.0)
        self.assertEqual(msg["metric"], "humidity_rh")
        self.assertEqual(msg["unit"], "%RH")

    def test_bundle_expands_to_multiple_publishes(self):
        client = MagicMock()
        bundle = {
            "schema": config.SCHEMA_RAW_BUNDLE,
            "asset": "crac-07",
            "ts": "2026-05-21T12:00:00Z",
            "readings": [
                {"metric": "supply_temp_c", "value": 40.0, "unit": "C"},
                {"metric": "humidity_rh", "value": 55.0, "unit": "%RH"},
                {"metric": "motor_vibration_mm_s", "value": 0.5, "unit": "mm/s"},
            ],
        }
        msg = MagicMock(
            topic="dc/Hub/v1/raw/dc1/h/a/r/crac-07/_bundle",
            payload=json.dumps(bundle).encode(),
        )
        deadband.on_message(client, None, msg)
        self.assertEqual(client.publish.call_count, 3)
        point_ids = set()
        for call in client.publish.call_args_list:
            msg = json.loads(call[0][1])
            point_ids.add(msg["pointId"])
        self.assertEqual(
            point_ids,
            {
                "crac-07:supply_temp_c",
                "crac-07:humidity_rh",
                "crac-07:motor_vibration_mm_s",
            },
        )

    def test_legacy_sensor_id_still_forwards(self):
        client = MagicMock()
        deadband.process_observation(
            client,
            {"sensorId": "m1-temp-inlet", "machineId": "machine-001", "temperature": 38.0},
        )
        msg = json.loads(client.publish.call_args[0][1])
        self.assertEqual(msg["sensorId"], "m1-temp-inlet")
        self.assertEqual(msg["value"], 38.0)


if __name__ == "__main__":
    unittest.main()
