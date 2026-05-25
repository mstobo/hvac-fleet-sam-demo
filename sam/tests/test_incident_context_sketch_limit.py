#!/usr/bin/env python3
"""Tests for fleet vs point get_incident_context sketch limits."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fleet_query_tools import (  # noqa: E402
    INCIDENT_CONTEXT_SKETCH_LIMIT,
    incident_context_sketch_limit,
    _is_fleet_machine_scope,
)


class IncidentContextSketchLimitTests(unittest.TestCase):
    def setUp(self):
        self._fleet = os.environ.get("FLEET_INCIDENT_CONTEXT_SKETCH_LIMIT")
        self._default = os.environ.get("INCIDENT_CONTEXT_SKETCH_LIMIT")
        for key in ("FLEET_INCIDENT_CONTEXT_SKETCH_LIMIT", "INCIDENT_CONTEXT_SKETCH_LIMIT"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key, val in (
            ("FLEET_INCIDENT_CONTEXT_SKETCH_LIMIT", self._fleet),
            ("INCIDENT_CONTEXT_SKETCH_LIMIT", self._default),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_fleet_machine_scope(self):
        self.assertTrue(_is_fleet_machine_scope("machine-001"))
        self.assertTrue(_is_fleet_machine_scope("Machine-003"))
        self.assertFalse(_is_fleet_machine_scope("machine-003:motor_temp_c"))
        self.assertFalse(_is_fleet_machine_scope("m3-temp-motor"))

    def test_default_limits(self):
        self.assertEqual(incident_context_sketch_limit("machine-002"), 10)
        self.assertEqual(
            incident_context_sketch_limit("machine-003:motor_temp_c"),
            INCIDENT_CONTEXT_SKETCH_LIMIT,
        )

    def test_env_overrides(self):
        os.environ["FLEET_INCIDENT_CONTEXT_SKETCH_LIMIT"] = "6"
        os.environ["INCIDENT_CONTEXT_SKETCH_LIMIT"] = "30"
        self.assertEqual(incident_context_sketch_limit("machine-001"), 6)
        self.assertEqual(incident_context_sketch_limit("m1-temp-motor"), 30)


if __name__ == "__main__":
    unittest.main()
