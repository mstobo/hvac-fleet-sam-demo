#!/usr/bin/env python3
import os
import unittest

import sketch_styles


class SketchStylesTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SKETCH_STYLE", None)

    def test_nl_default(self):
        os.environ.pop("SKETCH_STYLE", None)
        text = sketch_styles.render_sketch_text(
            point_id="machine-001:motor_temp_c",
            asset_id="machine-001",
            metric_id="motor_temp_c",
            temperature=80.0,
            zone="CRITICAL",
            delta_pct=0.06,
            forwarded_reason="delta",
            win_mean=76.0,
            win_min=74.0,
            win_max=82.0,
        )
        self.assertIn("recorded a", text)
        self.assertIn("Anomaly detected", text)

    def test_jargon_style(self):
        os.environ["SKETCH_STYLE"] = "jargon"
        text = sketch_styles.render_sketch_text(
            point_id="machine-001:motor_temp_c",
            asset_id="machine-001",
            metric_id="motor_temp_c",
            temperature=80.0,
            zone="CRITICAL",
            delta_pct=0.06,
            forwarded_reason="delta",
            win_mean=76.0,
            win_min=74.0,
            win_max=82.0,
        )
        self.assertIn("m1:mot", text)
        self.assertIn("!CRIT", text)
        self.assertNotIn("recorded a", text)

    def test_agent_context_includes_legend(self):
        os.environ["SKETCH_STYLE"] = "jargon"
        ctx = sketch_styles.sketch_context_for_agents()
        self.assertEqual(ctx["sketch_style"], "jargon")
        self.assertIn("sketch_legend", ctx)


if __name__ == "__main__":
    unittest.main()
