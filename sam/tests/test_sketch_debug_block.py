import os
import unittest

from fleet_query_tools import (
    INCIDENT_CONTEXT_SKETCH_LIMIT,
    _build_sketch_debug_block,
    _debug_sketch_evidence_enabled,
)


class SketchDebugBlockTests(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("FLEET_QUERY_DEBUG_SKETCH_EVIDENCE")
        os.environ["FLEET_QUERY_DEBUG_SKETCH_EVIDENCE"] = "true"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("FLEET_QUERY_DEBUG_SKETCH_EVIDENCE", None)
        else:
            os.environ["FLEET_QUERY_DEBUG_SKETCH_EVIDENCE"] = self._prev

    def test_disabled_by_default_without_env(self):
        os.environ["FLEET_QUERY_DEBUG_SKETCH_EVIDENCE"] = "false"
        self.assertFalse(_debug_sketch_evidence_enabled())
        self.assertIsNone(_build_sketch_debug_block(5))

    def test_at_limit_note(self):
        block = _build_sketch_debug_block(
            INCIDENT_CONTEXT_SKETCH_LIMIT,
            limit=INCIDENT_CONTEXT_SKETCH_LIMIT,
            sensor_id="machine-003:motor_temp_c",
        )
        self.assertTrue(block["sketch_evidence_at_limit"])
        self.assertEqual(len(block["section_7_lines"]), 1)
        self.assertIn("tool limit 25", block["section_7_lines"][0])
        self.assertIn("machine-003:motor_temp_c", block["section_7_lines"][0])

    def test_verbose_section7_two_lines(self):
        os.environ["FLEET_QUERY_SKETCH_SECTION7_VERBOSE"] = "true"
        block = _build_sketch_debug_block(3, limit=25, sensor_id="machine-001")
        self.assertEqual(len(block["section_7_lines"]), 2)
        os.environ.pop("FLEET_QUERY_SKETCH_SECTION7_VERBOSE", None)

    def test_zero_sketches(self):
        block = _build_sketch_debug_block(0, limit=25, sensor_id="machine-003")
        self.assertTrue(block["insufficient_sketch_context"])
        self.assertIn("Insufficient sketch context: Yes", block["section_7_lines"][0])


if __name__ == "__main__":
    unittest.main()
