#!/usr/bin/env python3
"""
fleet_alert_analyzer.py
=======================
Smart auto-trigger for LLM analysis on FLEET_CRITICAL events.

Design principles:
  1. Debounce: Wait 60s after first critical before triggering LLM
  2. Rate limit: Max 1 LLM analysis per 5 minutes
  3. Fleet-level only: Only trigger on FLEET_CRITICAL (multiple sensors)
  4. Batch: Collect all criticals during debounce window into one query

This module publishes to sensors/fleet/analysis-request topic.
SAM's Event Mesh Gateway picks this up and routes to FleetQueryAgent.
Response is delivered via Slack or response topic.

After a successful analysis-request publish, a JSON sketch audit report is also
published to sensors/fleet/audit-report (override with FLEET_AUDIT_REPORT_TOPIC)
for archival pipelines (e.g. S3). See fleet_sketch_audit_report.py — deterministic
only; SAM is not used for that report (by design, for now).

This is the appropriate use of event-triggered AI:
  - LOW frequency (maybe 1-5 per day)
  - HIGH value (correlated failures need immediate analysis)
"""

import argparse
import json
import os
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import paho.mqtt.client as mqtt


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# Import shared config
try:
    import pipeline_config as config
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False

if CONFIG_AVAILABLE:
    log = config.get_logger("AutoAnalysis")
else:
    import logging as _logging
    log = _logging.getLogger("AutoAnalysis")

# ── Configuration ────────────────────────────────────────────────────────────
DEBOUNCE_SECONDS = float(os.getenv("ANALYSIS_DEBOUNCE_SECONDS", "60.0"))
RATE_LIMIT_SECONDS = float(os.getenv("ANALYSIS_RATE_LIMIT_SECONDS", "300.0"))
ENABLE_AUTO_ANALYSIS = os.getenv("ENABLE_AUTO_ANALYSIS", "true").lower() in ("true", "1", "yes")
ANALYSIS_REQUEST_QOS = int(os.getenv("ANALYSIS_REQUEST_QOS", "1"))
PUBLISH_ACK_TIMEOUT_SECONDS = float(os.getenv("ANALYSIS_PUBLISH_ACK_TIMEOUT_SECONDS", "8.0"))
PUBLISH_RETRY_COUNT = int(os.getenv("ANALYSIS_PUBLISH_RETRY_COUNT", "2"))

# MQTT topic for analysis requests (Event Mesh Gateway subscribes to this)
ANALYSIS_REQUEST_TOPIC = "sensors/fleet/analysis-request"
ANALYSIS_RESPONSE_TOPIC = "sensors/fleet/analysis-response"

# Post-incident sketch audit (JSON for S3/archival consumers; published alongside analysis request)
ENABLE_FLEET_SKETCH_AUDIT = os.getenv("ENABLE_FLEET_SKETCH_AUDIT", "true").lower() in (
    "true",
    "1",
    "yes",
)
AUDIT_REPORT_TOPIC = os.getenv("FLEET_AUDIT_REPORT_TOPIC", "sensors/fleet/audit-report")
AUDIT_WINDOW_DAYS = int(os.getenv("FLEET_AUDIT_SKETCH_DAYS", "3"))
AUDIT_REPORT_QOS = int(os.getenv("AUDIT_REPORT_QOS", "1"))

# Broker config (use pipeline_config if available)
if CONFIG_AVAILABLE:
    BROKER_HOST = config.BROKER_HOST
    BROKER_PORT = config.BROKER_PORT
    USERNAME = config.USERNAME
    PASSWORD = config.PASSWORD
    USE_TLS = config.USE_TLS
else:
    BROKER_HOST = os.getenv("SOLACE_HOST", "localhost")
    BROKER_PORT = int(os.getenv("SOLACE_PORT", "8883"))
    USERNAME = os.getenv("SOLACE_USER", "")
    PASSWORD = os.getenv("SOLACE_PASS", "")
    USE_TLS = os.getenv("SOLACE_TLS", "true").lower() in ("true", "1", "yes")

# ── State ────────────────────────────────────────────────────────────────────
_last_analysis_time = 0.0
_pending_analysis = False
_pending_timer: Optional[threading.Timer] = None
_collected_criticals = []
_collected_sensors = []
_lock = threading.Lock()
_mqtt_client: Optional[mqtt.Client] = None
_mqtt_connected = False
_publish_ack_events = {}
_publish_ack_lock = threading.Lock()


def _normalize_notes(notes: str) -> str:
    """Remove explicit test-language from outbound analysis notes."""
    text = (notes or "").strip()
    if not text:
        return "Multiple sensors in critical state"
    lowered = text.lower()
    if any(token in lowered for token in ("test", "validation", "e2e", "end-to-end", "verify")):
        return "Multiple sensors in critical state"
    return text


def _get_mqtt_client() -> Optional[mqtt.Client]:
    """Get or create MQTT client for publishing analysis requests."""
    global _mqtt_client, _mqtt_connected

    if _mqtt_client is not None and _mqtt_connected:
        return _mqtt_client

    if CONFIG_AVAILABLE:
        config.validate_broker_config()

    try:
        def on_connect(client, userdata, flags, reason_code, properties=None):
            global _mqtt_connected
            if reason_code == 0:
                log.info("MQTT connected to %s", BROKER_HOST)
                _mqtt_connected = True
                client.subscribe(ANALYSIS_RESPONSE_TOPIC)
            else:
                log.error("MQTT connection failed rc=%s", reason_code)
                _mqtt_connected = False

        def on_message(client, userdata, msg):
            # Log responses (they also go to Slack via gateway)
            log.info("response received on %s", msg.topic)
            try:
                response = json.loads(msg.payload.decode())
                log.debug("response preview: %s", str(response)[:200])
            except (ValueError, TypeError):
                log.debug("response: %s", msg.payload.decode()[:200])

        def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
            global _mqtt_connected
            _mqtt_connected = False
            log.warning("MQTT disconnected rc=%s", reason_code)

        def on_publish(client, userdata, mid, reason_code, properties=None):
            _ = (client, userdata, reason_code, properties)
            with _publish_ack_lock:
                event = _publish_ack_events.get(mid)
            if event is not None:
                event.set()
        
        _mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"fleet-analyzer-{int(time.time())}",
            protocol=mqtt.MQTTv5
        )
        _mqtt_client.username_pw_set(USERNAME, PASSWORD)
        _mqtt_client.on_connect = on_connect
        _mqtt_client.on_message = on_message
        _mqtt_client.on_disconnect = on_disconnect
        _mqtt_client.on_publish = on_publish
        
        if USE_TLS:
            _mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
        
        _mqtt_client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
        _mqtt_client.loop_start()
        
        # Wait briefly for connection
        for _ in range(10):
            if _mqtt_connected:
                break
            time.sleep(0.1)
        
        return _mqtt_client
        
    except Exception:
        log.exception("failed to create MQTT client")
        return None


def _should_analyze() -> bool:
    """Check if we should trigger analysis (rate limit check)."""
    now = time.time()
    if now - _last_analysis_time < RATE_LIMIT_SECONDS:
        remaining = RATE_LIMIT_SECONDS - (now - _last_analysis_time)
        log.info("rate limited; next analysis allowed in %.0fs", remaining)
        return False
    return True


def _build_analysis_event(criticals: list, sensors: list) -> dict:
    """Build the event payload for the analysis request."""
    # Unique sensor IDs from debounce-window samples plus any fleet snapshot lists
    # (e.g. affected_sensor_ids from anomaly_service when FLEET_CRITICAL fires).
    from_sensors = [s.get("sensor_id") for s in sensors if s.get("sensor_id")]
    extra: list[str] = []
    for c in criticals:
        ids = c.get("affected_sensor_ids")
        if isinstance(ids, list):
            extra.extend(str(x) for x in ids if x)
    sensor_ids = sorted(set(from_sensors + extra))
    
    # Get latest fleet critical info
    latest_critical = criticals[-1] if criticals else {}
    
    # Calculate average temperature from collected sensors
    temps = [s.get("temperature", 0) for s in sensors if s.get("temperature")]
    avg_temp = sum(temps) / len(temps) if temps else 0
    
    return {
        "event_type": "FLEET_CRITICAL_ANALYSIS_REQUEST",
        "fleet_status": latest_critical.get("fleet_status", "FLEET_CRITICAL"),
        "critical_count": latest_critical.get("critical_count", len(sensor_ids)),
        "active_sensors": latest_critical.get("active_sensors", len(sensor_ids)),
        "sensors": sensor_ids,
        "affected_sensor_ids": sensor_ids,
        "average_temperature": round(avg_temp, 1),
        "notes": _normalize_notes(latest_critical.get("notes", "Multiple sensors in critical state")),
        "timestamp": _utc_now_iso_z(),
        "debounce_window_seconds": DEBOUNCE_SECONDS,
        "events_collected": len(criticals) + len(sensors),
    }


def _publish_with_puback(client: mqtt.Client, topic: str, payload: str, qos: int, log_label: str) -> bool:
    """Publish with QoS>=1 and wait for broker PUBACK (same pattern as analysis request)."""
    for attempt in range(1, PUBLISH_RETRY_COUNT + 2):
        result = client.publish(topic, payload, qos=qos)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("%s publish attempt %d failed rc=%s", log_label, attempt, result.rc)
            time.sleep(0.4)
            continue

        ack_event = threading.Event()
        with _publish_ack_lock:
            _publish_ack_events[result.mid] = ack_event

        acked = ack_event.wait(timeout=PUBLISH_ACK_TIMEOUT_SECONDS)
        with _publish_ack_lock:
            _publish_ack_events.pop(result.mid, None)

        if acked:
            log.info(
                "published %s to %s (qos=%s, mid=%s, attempt=%d)",
                log_label, topic, qos, result.mid, attempt,
            )
            return True

        log.warning(
            "%s attempt %d no PUBACK within %.1fs (mid=%s)",
            log_label, attempt, PUBLISH_ACK_TIMEOUT_SECONDS, result.mid,
        )
        time.sleep(0.4)

    return False


def _execute_analysis():
    """Execute the LLM analysis by publishing to Event Mesh Gateway topic."""
    global _last_analysis_time, _pending_analysis, _collected_criticals, _collected_sensors
    
    with _lock:
        if not _collected_criticals and not _collected_sensors:
            log.info("no events collected, skipping analysis")
            _pending_analysis = False
            return

        criticals = _collected_criticals.copy()
        sensors = _collected_sensors.copy()
        _collected_criticals = []
        _collected_sensors = []
        _pending_analysis = False

    total_events = len(criticals) + len(sensors)
    log.info("triggering LLM analysis for %d collected events", total_events)

    event = _build_analysis_event(criticals, sensors)
    correlation_id = str(uuid.uuid4())
    event["correlation_id"] = correlation_id

    client = _get_mqtt_client()
    if client is None:
        log.error("no MQTT client available")
        return

    try:
        payload = json.dumps(event)
        published = _publish_with_puback(
            client,
            ANALYSIS_REQUEST_TOPIC,
            payload,
            ANALYSIS_REQUEST_QOS,
            "analysis-request",
        )

        if published:
            _last_analysis_time = time.time()
            log.debug("payload: %s", json.dumps(event))
            log.info("SAM Event Mesh Gateway will route to FleetQueryAgent; response → Slack if configured")

            if ENABLE_FLEET_SKETCH_AUDIT:
                try:
                    from fleet_sketch_audit_report import build_fleet_sketch_audit_report

                    report = build_fleet_sketch_audit_report(
                        event,
                        sensors,
                        correlation_id=correlation_id,
                        days=AUDIT_WINDOW_DAYS,
                    )
                    audit_ok = _publish_with_puback(
                        client,
                        AUDIT_REPORT_TOPIC,
                        json.dumps(report),
                        AUDIT_REPORT_QOS,
                        "sketch-audit-report",
                    )
                    if audit_ok:
                        log.info(
                            "sketch audit report (%dd) on %s (correlation_id=%s)",
                            AUDIT_WINDOW_DAYS, AUDIT_REPORT_TOPIC, correlation_id,
                        )
                    else:
                        log.warning("sketch audit report not acknowledged; check broker or AUDIT_REPORT_QOS")
                except Exception:
                    log.exception("sketch audit failed (non-fatal)")
        else:
            log.error("failed to deliver analysis request after retries; not acknowledged by broker")

    except Exception:
        log.exception("error publishing analysis request")


def on_fleet_critical(fleet_status: str, critical_count: int, active_sensors: int, 
                       notes: str, sensor_data: dict = None):
    """
    Called by anomaly_service when fleet status is FLEET_CRITICAL.
    
    Implements debounce: First call starts a timer, subsequent calls add to the batch.
    After DEBOUNCE_SECONDS, publishes analysis request to Event Mesh Gateway.
    
    Args:
        fleet_status: Current fleet status (FLEET_CRITICAL, CRITICAL, etc.)
        critical_count: Number of sensors in critical state
        active_sensors: Total active sensors
        notes: Description of the fleet status
        sensor_data: Optional dict with sensor details
    """
    global _pending_analysis, _pending_timer
    
    if not ENABLE_AUTO_ANALYSIS:
        return
    
    # Only auto-analyze FLEET_CRITICAL (correlated events)
    if fleet_status != "FLEET_CRITICAL":
        return
    
    with _lock:
        # Collect this critical event
        _collected_criticals.append({
            "fleet_status": fleet_status,
            "critical_count": critical_count,
            "active_sensors": active_sensors,
            "notes": notes,
            "timestamp": _utc_now_iso(),
            **(sensor_data or {})
        })
        
        # If already pending, just add to batch
        if _pending_analysis:
            log.info(
                "added to pending batch (%d fleet events, %d sensor events)",
                len(_collected_criticals), len(_collected_sensors),
            )
            return

        # Check rate limit before starting debounce
        if not _should_analyze():
            _collected_criticals.clear()
            _collected_sensors.clear()
            return

        # Start debounce timer
        _pending_analysis = True
        log.info("FLEET_CRITICAL detected; debounce=%ss; will publish to %s", DEBOUNCE_SECONDS, ANALYSIS_REQUEST_TOPIC)
        
        _pending_timer = threading.Timer(DEBOUNCE_SECONDS, _execute_analysis)
        _pending_timer.daemon = True
        _pending_timer.start()


def on_sensor_critical(sensor_id: str, temperature: float, zone: str):
    """
    Called when an individual sensor goes critical.
    Used to collect sensor details for the batch analysis.
    """
    if not ENABLE_AUTO_ANALYSIS:
        return
    
    with _lock:
        _collected_sensors.append({
            "sensor_id": sensor_id,
            "temperature": temperature,
            "zone": zone,
            "timestamp": _utc_now_iso()
        })
        
        if _pending_analysis:
            log.debug("collected sensor: %s @ %.1f°C", sensor_id, temperature)


def get_status() -> dict:
    """Get current status of the auto-analyzer."""
    with _lock:
        now = time.time()
        time_since_last = now - _last_analysis_time if _last_analysis_time > 0 else None
        time_until_allowed = max(0, RATE_LIMIT_SECONDS - time_since_last) if time_since_last else 0
        
        return {
            "enabled": ENABLE_AUTO_ANALYSIS,
            "pending_analysis": _pending_analysis,
            "collected_fleet_events": len(_collected_criticals),
            "collected_sensor_events": len(_collected_sensors),
            "last_analysis_ago_seconds": time_since_last,
            "next_analysis_allowed_in": time_until_allowed,
            "debounce_seconds": DEBOUNCE_SECONDS,
            "rate_limit_seconds": RATE_LIMIT_SECONDS,
            "mqtt_connected": _mqtt_connected,
            "request_topic": ANALYSIS_REQUEST_TOPIC,
            "response_topic": ANALYSIS_RESPONSE_TOPIC,
            "sketch_audit_enabled": ENABLE_FLEET_SKETCH_AUDIT,
            "audit_report_topic": AUDIT_REPORT_TOPIC,
            "audit_window_days": AUDIT_WINDOW_DAYS,
        }


def shutdown():
    """Clean shutdown of MQTT client."""
    global _mqtt_client
    if _mqtt_client:
        _mqtt_client.loop_stop()
        _mqtt_client.disconnect()
        _mqtt_client = None


def _seed_test_collections() -> None:
    """
    Fill collected critical/sensor lists like a FLEET_CRITICAL batch, without starting
    the debounce timer (for manual / CLI testing).
    """
    global _collected_criticals, _collected_sensors, _pending_analysis, _pending_timer
    with _lock:
        if _pending_timer is not None:
            _pending_timer.cancel()
            _pending_timer = None
        _collected_criticals = []
        _collected_sensors = []
        _pending_analysis = False

    on_sensor_critical("sensor-001", 68.5, "CRITICAL")
    on_sensor_critical("sensor-002", 67.2, "CRITICAL")
    on_sensor_critical("sensor-003", 69.1, "CRITICAL")

    ts = _utc_now_iso()
    with _lock:
        _collected_criticals.append(
            {
                "fleet_status": "FLEET_CRITICAL",
                "critical_count": 3,
                "active_sensors": 3,
                "notes": "CLI self-test batch for analysis-request and audit-report",
                "timestamp": ts,
            }
        )


def publish_audit_report_test_only() -> bool:
    """
    Publish one FLEET_SKETCH_AUDIT_REPORT JSON to AUDIT_REPORT_TOPIC only (no analysis-request).
    Use this to validate subscribers on sensors/fleet/audit-report without involving SAM.
    """
    from fleet_sketch_audit_report import build_fleet_sketch_audit_report

    correlation_id = str(uuid.uuid4())
    ts = _utc_now_iso()
    criticals = [
        {
            "fleet_status": "FLEET_CRITICAL",
            "critical_count": 3,
            "active_sensors": 9,
            "notes": "Audit-only self-test for MQTT subscribers",
            "timestamp": ts,
        }
    ]
    sensors = [
        {
            "sensor_id": sid,
            "temperature": temp,
            "zone": "CRITICAL",
            "timestamp": ts,
        }
        for sid, temp in (
            ("sensor-001", 68.5),
            ("sensor-002", 67.2),
            ("sensor-003", 69.1),
        )
    ]
    event = _build_analysis_event(criticals, sensors)
    event["correlation_id"] = correlation_id
    report = build_fleet_sketch_audit_report(
        event,
        sensors,
        correlation_id=correlation_id,
        days=AUDIT_WINDOW_DAYS,
    )
    client = _get_mqtt_client()
    if client is None:
        log.error("no MQTT client; cannot publish audit-only report")
        return False
    time.sleep(1.0)
    ok = _publish_with_puback(
        client,
        AUDIT_REPORT_TOPIC,
        json.dumps(report),
        AUDIT_REPORT_QOS,
        "sketch-audit-report-only",
    )
    if ok:
        log.info("audit-only report published to %s", AUDIT_REPORT_TOPIC)
    return ok


# For testing: cd sam && python src/fleet_alert_analyzer.py [--now] [--audit-only]
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test fleet auto-analysis and/or sketch audit MQTT publishes.",
    )
    parser.add_argument(
        "--now",
        action="store_true",
        help="Skip debounce wait; publish analysis-request + audit-report after connect (~2s).",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Publish only to audit-report topic (no analysis-request / SAM).",
    )
    args = parser.parse_args()

    print("Testing fleet_alert_analyzer (Event Mesh Gateway version)...")
    print(f"Status: {json.dumps(get_status(), indent=2)}")

    if args.audit_only:
        if not ENABLE_FLEET_SKETCH_AUDIT:
            print("ENABLE_FLEET_SKETCH_AUDIT is false; enable it to publish audit reports.")
            raise SystemExit(1)
        print("\nPublishing audit-report only (no analysis-request)...")
        publish_audit_report_test_only()
        print(f"\nFinal status: {json.dumps(get_status(), indent=2)}")
        shutdown()
        print("Done")
        raise SystemExit(0)

    print("\nSimulating FLEET_CRITICAL batch...")
    if args.now:
        _seed_test_collections()
        print(f"\nStatus after seed: {json.dumps(get_status(), indent=2)}")
        print("\n--now: publishing immediately (no debounce sleep)...")
        time.sleep(1.5)
        _execute_analysis()
        time.sleep(2.0)
    else:
        on_sensor_critical("sensor-001", 68.5, "CRITICAL")
        on_sensor_critical("sensor-002", 67.2, "CRITICAL")
        on_sensor_critical("sensor-003", 69.1, "CRITICAL")
        on_fleet_critical(
            fleet_status="FLEET_CRITICAL",
            critical_count=3,
            active_sensors=3,
            notes="CLI debounced test - 3 sensors in critical state",
        )
        print(f"\nStatus after trigger: {json.dumps(get_status(), indent=2)}")
        print(f"\nWaiting {DEBOUNCE_SECONDS}s for debounce...")
        time.sleep(DEBOUNCE_SECONDS + 5)

    print(f"\nFinal status: {json.dumps(get_status(), indent=2)}")
    shutdown()
    print("Done")
