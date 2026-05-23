#!/usr/bin/env python3
"""Tests for fleet_analysis_response parsing and token extraction."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fleet_analysis_response import (  # noqa: E402
    compress_fleet_sketch_section,
    normalize_fleet_chart_links,
    extract_llm_usage_from_task_response,
    format_llm_usage_footer,
    format_slack_analysis_body,
    is_failed_task_response,
    parse_analysis_response_payload,
)


class FleetAnalysisResponseTests(unittest.TestCase):
    def test_plain_text_legacy(self):
        body = "## Fleet report\nAll clear."
        text, usage, meta = parse_analysis_response_payload(body)
        self.assertEqual(text, body)
        self.assertIsNone(usage)
        self.assertEqual(meta["payload_format"], "text")

    def test_task_response_metadata_totals(self):
        payload = {
            "text": "## Section 1\nDetails.",
            "a2a_task_response": {
                "id": "task-fleet-001",
                "metadata": {
                    "total_input_tokens": 42000,
                    "total_output_tokens": 1800,
                    "total_cached_input_tokens": 12000,
                },
            },
        }
        text, usage, meta = parse_analysis_response_payload(json.dumps(payload))
        self.assertIn("Section 1", text)
        self.assertEqual(meta["payload_format"], "json")
        self.assertIsNotNone(usage)
        self.assertEqual(usage["prompt_tokens"], 42000)
        self.assertEqual(usage["completion_tokens"], 1800)
        self.assertEqual(usage["cached_tokens"], 12000)
        self.assertEqual(usage["total_tokens"], 43800)
        self.assertEqual(usage["task_id"], "task-fleet-001")

    def test_token_usage_details_by_model(self):
        payload = {
            "text": "Brief.",
            "a2a_task_response": {
                "metadata": {
                    "token_usage_details": {
                        "by_model": {
                            "azure-gpt-4o": {
                                "input_tokens": 1000,
                                "output_tokens": 200,
                            },
                            "azure-gpt-4o-mini": {
                                "input_tokens": 500,
                                "output_tokens": 50,
                            },
                        }
                    }
                }
            },
        }
        _text, usage, _meta = parse_analysis_response_payload(json.dumps(payload))
        self.assertEqual(usage["prompt_tokens"], 1500)
        self.assertEqual(usage["completion_tokens"], 250)
        self.assertEqual(usage["total_tokens"], 1750)

    def test_format_usage_footer(self):
        footer = format_llm_usage_footer(
            {
                "prompt_tokens": 12345,
                "completion_tokens": 890,
                "cached_tokens": 0,
                "total_tokens": 13235,
                "task_id": "task-abc",
            }
        )
        self.assertIn("13,235 tokens", footer)
        self.assertIn("12,345 in", footer)
        self.assertIn("task-abc", footer)

    def test_format_usage_footer_empty_when_missing(self):
        self.assertEqual(format_llm_usage_footer(None), "")

    def test_extract_direct_helper(self):
        usage = extract_llm_usage_from_task_response(
            {"llm_usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        )
        self.assertEqual(usage["total_tokens"], 15)

    def test_normalize_fleet_chart_links_strips_per_point_urls(self):
        noisy = (
            "1) Summary\n"
            "### machine-001:inlet_temp_c\n"
            "- Chart: machine-001:inlet_temp_c: http://host/charts/plotly-html?sensor_id=machine-001%3Ainlet\n"
            "Chart Evidence:\n"
            "- machine-001: http://host/charts/machine-plotly-html?asset_id=machine-001\n"
            "- machine-002: http://host/charts/machine-plotly-html?asset_id=machine-002\n"
        )
        out = normalize_fleet_chart_links(noisy)
        self.assertNotIn("sensor_id=", out)
        self.assertIn("machine-plotly-html", out)

    def test_compress_fleet_sketch_section(self):
        noisy = (
            "7) Data Gaps\n"
            "Sketch evidence: 25 sketch(es) returned for machine-001:inlet_temp_c (tool limit 25)\n"
            "Insufficient sketch context: No — sketch narratives were included.\n"
            "Sketch evidence: 25 sketch(es) returned for machine-001:motor_temp_c (tool limit 25)\n"
            "Insufficient sketch context: No — sketch narratives were included.\n"
            "Sketch evidence: 25 sketch(es) returned for machine-002:inlet_temp_c (tool limit 25)\n"
            "Insufficient sketch context: No — sketch narratives were included.\n"
            "Sketch evidence: 25 sketch(es) returned for machine-002:motor_temp_c (tool limit 25)\n"
            "8) Dispatch recommendations\n"
        )
        out = compress_fleet_sketch_section(noisy)
        self.assertEqual(out.count("Sketch evidence: 25 sketch"), 0)
        self.assertIn("Sketch evidence (by machine): machine-001=25, machine-002=25", out)
        self.assertEqual(out.count("Insufficient sketch context"), 1)
        self.assertIn("8) Dispatch", out)

    def test_deep_nested_metadata(self):
        payload = {
            "text": "Report body",
            "a2a_task_response": {
                "id": "gdk-task-nested",
                "result": {
                    "metadata": {
                        "token_usage_details": {
                            "by_model": {
                                "production-models": {
                                    "input_tokens": 9000,
                                    "output_tokens": 1200,
                                }
                            }
                        }
                    }
                },
            },
        }
        slack = format_slack_analysis_body(json.dumps(payload), trace_id="fa-tokens")
        self.assertIn("10,200 tokens", slack)
        self.assertIn("9,000 in", slack)

    def test_auth_failure_task_response(self):
        payload = {
            "text": "The LLM service rejected the authentication credentials.",
            "a2a_task_response": {
                "id": "gdk-task-48a4132edc234bcbb2f2d2018d97f2f9",
                "status": {"state": "failed"},
            },
        }
        raw = json.dumps(payload)
        report, _usage, meta = parse_analysis_response_payload(raw)
        self.assertIn("authentication credentials", report)
        self.assertTrue(meta.get("task_failed"))
        self.assertTrue(is_failed_task_response(payload, report))
        slack = format_slack_analysis_body(raw, trace_id="fa-test")
        self.assertIn("Automated Fleet Analysis failed", slack)
        self.assertNotIn('"a2a_task_response"', slack)
        self.assertIn("test_llm.py", slack)


    def test_rewrite_chart_placeholder_to_url(self):
        import os

        os.environ["CHART_PUBLIC_BASE_URL"] = "http://demo.example/charts"
        os.environ["CHART_QUERY_API_KEY"] = "demo-chart-key"
        try:
            from fleet_query_tools import rewrite_chart_urls_in_text

            text = (
                "Chart: (machine-002:motor_temp_c) plot window "
                "2026-05-23T11:07:01Z → 2026-05-23T13:07:01Z."
            )
            out = rewrite_chart_urls_in_text(text)
            self.assertIn("http://demo.example/charts/plotly-html", out)
            self.assertIn("machine-002%3Amotor_temp_c", out)
            self.assertIn("key=demo-chart-key", out)
            self.assertNotIn("plot window 2026", out)
        finally:
            os.environ.pop("CHART_PUBLIC_BASE_URL", None)
            os.environ.pop("CHART_QUERY_API_KEY", None)

    def test_rewrite_chart_spec_generated_bullets(self):
        import os

        os.environ["CHART_PUBLIC_BASE_URL"] = "http://demo.example/charts"
        try:
            from fleet_query_tools import rewrite_chart_urls_in_text

            text = (
                "Chart Evidence:\n"
                "- machine-002: inlet temp chart (max_v) - chart spec generated for "
                "2026-05-23T11:47:22Z → 2026-05-23T13:47:22Z.\n"
                "- machine-003: motor temp chart (max_v) - chart spec generated for "
                "2026-05-23T11:47:22Z → 2026-05-23T13:47:22Z."
            )
            out = rewrite_chart_urls_in_text(text)
            self.assertIn("http://demo.example/charts/plotly-html", out)
            self.assertIn("machine-002%3Ainlet_temp_c", out)
            self.assertIn("machine-003%3Amotor_temp_c", out)
            self.assertIn("value_key=avg_v", out)
            self.assertNotIn("chart spec generated", out)
        finally:
            os.environ.pop("CHART_PUBLIC_BASE_URL", None)

    def test_inject_chart_links_under_point_headings(self):
        import os

        os.environ["CHART_PUBLIC_BASE_URL"] = "http://demo.example/charts"
        try:
            from fleet_query_tools import inject_fleet_analysis_chart_links

            text = (
                "Chart Evidence: plot specs requested for the 120-minute UTC window "
                "2026-05-23T12:02:05Z → 2026-05-23T14:02:05Z "
                "(note: chart service returned plot specs but no pinned public URLs in this run).\n\n"
                "### machine-002:inlet_temp_c\n"
                "- Current ~73.9°C CRITICAL.\n\n"
                "### machine-003:motor_temp_c\n"
                "- Sustained CRITICAL motor temp.\n"
            )
            out = inject_fleet_analysis_chart_links(text)
            self.assertIn("plotly-html", out)
            self.assertIn("machine-002%3Ainlet_temp_c", out)
            self.assertIn("machine-003%3Amotor_temp_c", out)
            self.assertNotIn("no pinned public URLs", out)
        finally:
            os.environ.pop("CHART_PUBLIC_BASE_URL", None)

    def test_rewrite_plot_window_bullets_and_heading_inject(self):
        import os

        os.environ["CHART_PUBLIC_BASE_URL"] = "http://demo.example/charts"
        try:
            from fleet_query_tools import rewrite_chart_urls_in_text

            text = (
                "Chart Evidence (UTC window 2026-05-23T12:24:49Z → 2026-05-23T14:24:49Z for machine-001; "
                "2026-05-23T12:24:54Z → 2026-05-23T14:24:54Z for machine-002):\n"
                "- machine-001:motor_temp_c plot (max_v window 2026-05-23T12:24:49Z → 2026-05-23T14:24:49Z).\n\n"
                "### machine-001:motor_temp_c\n"
                "- Current: 88.1°C CRITICAL.\n"
            )
            out = rewrite_chart_urls_in_text(text)
            self.assertIn("plotly-html", out)
            self.assertIn("machine-001%3Amotor_temp_c", out)
            self.assertNotIn("plot (max_v window", out)
            self.assertIn("- Chart:", out)
        finally:
            os.environ.pop("CHART_PUBLIC_BASE_URL", None)


if __name__ == "__main__":
    unittest.main()
