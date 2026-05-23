#!/usr/bin/env python3
"""
analysis_response_to_slack.py
=============================
Bridge service that subscribes to fleet-analysis response topics and posts
messages directly to Slack via the existing SlackNotifier utility.

Fleet-analysis gateway publishes JSON ``task_response`` objects (text + A2A
metadata). This bridge extracts the report body and appends an LLM token
usage footer when SAM stamped usage on the completed task.
"""

import os
import signal
import sys
import time
import hashlib

import paho.mqtt.client as mqtt

import pipeline_config as config
from fleet_analysis_response import format_slack_analysis_body, parse_analysis_response_payload
from fleet_query_tools import (
    chart_public_base_for_links,
    rewrite_chart_urls_in_text,
    _is_browser_unreachable_chart_base,
)
from slack_notifier import send_message


RESPONSE_TOPIC = os.getenv("ANALYSIS_RESPONSE_TOPIC", "sensors/fleet/analysis-response")
ERROR_TOPIC = os.getenv("ANALYSIS_ERROR_TOPIC", "sensors/fleet/analysis-error")
SLACK_CHANNEL = os.getenv("SLACK_ALERT_CHANNEL", "#sensor-alerts")

_running = True


def _trace_id(topic: str, payload_text: str) -> str:
    digest = hashlib.sha1(f"{topic}|{payload_text}".encode("utf-8")).hexdigest()[:10]
    return f"fa-{digest}"


def _format_message(topic: str, payload_text: str) -> str:
    trace_id = _trace_id(topic, payload_text)
    if topic == RESPONSE_TOPIC:
        return format_slack_analysis_body(
            payload_text,
            trace_id=trace_id,
            rewrite_urls=rewrite_chart_urls_in_text,
        )
    report_text, _, meta = parse_analysis_response_payload(payload_text)
    body = report_text.strip() or payload_text
    if meta.get("payload_format") == "json" and len(body) > 500:
        body = body[:500] + "\n…(truncated)"
    return f"*Automated Fleet Analysis Error*  \n`Trace ID: {trace_id}`\n{body}"


def on_connect(client: mqtt.Client, _userdata, _flags, reason_code, _properties=None) -> None:
    if reason_code == 0:
        print(f"[AnalysisSlackBridge] Connected to broker {config.BROKER_HOST}:{config.BROKER_PORT}")
        client.subscribe(RESPONSE_TOPIC, qos=1)
        client.subscribe(ERROR_TOPIC, qos=1)
        print(f"[AnalysisSlackBridge] Subscribed to {RESPONSE_TOPIC} and {ERROR_TOPIC}")
        return
    print(f"[AnalysisSlackBridge] MQTT connect failed: rc={reason_code}")


def on_disconnect(_client: mqtt.Client, _userdata, disconnect_flags, reason_code, _properties=None) -> None:
    _ = disconnect_flags
    print(f"[AnalysisSlackBridge] MQTT disconnected: rc={reason_code}")


def on_message(_client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
    text = msg.payload.decode("utf-8", errors="replace").strip()
    if not text:
        return

    formatted = _format_message(msg.topic, text)
    posted = send_message(formatted, channel=SLACK_CHANNEL)
    if posted:
        if msg.topic == RESPONSE_TOPIC:
            _report, usage, meta = parse_analysis_response_payload(text)
            if usage:
                print(
                    f"[AnalysisSlackBridge] Posted to Slack ({usage.get('total_tokens', 0)} LLM tokens, "
                    f"format={meta.get('payload_format')})"
                )
            else:
                print(
                    f"[AnalysisSlackBridge] Posted to Slack (no LLM usage in payload, "
                    f"format={meta.get('payload_format')})"
                )
        else:
            print(f"[AnalysisSlackBridge] Posted to Slack from topic {msg.topic}")
    else:
        print(f"[AnalysisSlackBridge] Slack post failed for topic {msg.topic}")


def _handle_signal(_sig, _frame) -> None:
    global _running
    _running = False


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    client = config.create_mqtt_client("analysis-response-to-slack")
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    print("[AnalysisSlackBridge] Starting...")
    chart_base = chart_public_base_for_links()
    if not chart_base or _is_browser_unreachable_chart_base(chart_base):
        print(
            "[AnalysisSlackBridge] WARN: Set CHART_PUBLIC_BASE_URL (or DASHBOARD_PUBLIC_HOST / "
            "EC2_PUBLIC_HOST) in deploy/aws/.env — fleet analysis chart links will be omitted"
        )
    else:
        print(f"[AnalysisSlackBridge] Chart links base: {chart_base}")
    print(f"[AnalysisSlackBridge] Slack channel: {SLACK_CHANNEL}")
    print(f"[AnalysisSlackBridge] Response topic: {RESPONSE_TOPIC}")
    print(f"[AnalysisSlackBridge] Error topic: {ERROR_TOPIC}")

    client.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while _running:
            time.sleep(0.5)
    finally:
        client.loop_stop()
        client.disconnect()
        print("[AnalysisSlackBridge] Stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
