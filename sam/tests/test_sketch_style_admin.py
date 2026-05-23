#!/usr/bin/env python3
import os
import tempfile
import unittest

import sketch_style_admin
import sketch_styles


class SketchStyleAdminTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._override = os.path.join(self._tmpdir, "sketch_style.override")
        os.environ["SKETCH_STYLE_OVERRIDE_PATH"] = self._override
        os.environ["SKETCH_STYLE"] = "nl"
        sketch_style_admin.clear_sketch_style_override()

    def tearDown(self):
        sketch_style_admin.clear_sketch_style_override()
        os.environ.pop("SKETCH_STYLE_OVERRIDE_PATH", None)
        os.environ.pop("SKETCH_STYLE", None)

    def test_override_wins_over_env(self):
        os.environ["SKETCH_STYLE"] = "nl"
        sketch_style_admin.set_sketch_style_override("jargon")
        self.assertEqual(sketch_styles.get_sketch_style(), "jargon")

    def test_status_payload(self):
        sketch_style_admin.set_sketch_style_override("jargon")
        status = sketch_style_admin.sketch_style_status()
        self.assertEqual(status["effective"], "jargon")
        self.assertTrue(status["persisted"])

    def test_normalize_aliases(self):
        self.assertEqual(sketch_style_admin.normalize_style("expert"), "jargon")
        self.assertEqual(sketch_style_admin.normalize_style("natural"), "nl")


if __name__ == "__main__":
    unittest.main()
