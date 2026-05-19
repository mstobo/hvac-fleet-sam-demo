#!/usr/bin/env python3
"""
pipeline_config.py
==================
Shared configuration for all pipeline microservices.

Environment variables:
  SOLACE_HOST  - Broker hostname
  SOLACE_PORT  - Broker port (default: 8883)
  SOLACE_USER  - Username
  SOLACE_PASS  - Password
  SOLACE_TLS   - Enable TLS (default: true)
  DC_BROKER_SITE - MQTT topic site segment after namespace (default: Hub).
                   Use Hub for the central broker; DC1 / DC2 for spokes.
  DC_PIPELINE_MULTISITE_RAW - If true, deadband subscribes dc/+/v1/raw/# and
                   publishes on DC_BROKER_SITE pipeline topics (hub aggregation).
"""

import logging
import os
import re
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import paho.mqtt.client as mqtt


# ── Logging ──────────────────────────────────────────────────────────────────
# Shared logger factory for pipeline services. Controlled by LOG_LEVEL env var
# (default INFO). Operators can flip to DEBUG to see every per-message
# disposition; production stays quiet at INFO.
_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_logging_configured = False


def _configure_logging_once() -> None:
    """Set up basicConfig the first time a logger is requested. Skipped if some other
    framework (SAM, pytest, custom dictConfig) has already attached a root handler —
    so this never clobbers a more sophisticated logging setup."""
    global _logging_configured
    if _logging_configured:
        return
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=_LOG_LEVEL,
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    _logging_configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger whose name is rendered as %(name)s in the format string,
    reproducing the historical [ServiceName] prefix without any per-message work."""
    _configure_logging_once()
    return logging.getLogger(name)


def _resolve_broker_from_env():
    """Align with SAM `.env` (SOLACE_BROKER_URL) and legacy SOLACE_HOST/SOLACE_USER."""
    host = (os.getenv("SOLACE_HOST") or "").strip()
    port_s = (os.getenv("SOLACE_PORT") or "").strip()
    user = (os.getenv("SOLACE_USER") or "").strip()
    password = (os.getenv("SOLACE_PASS") or "").strip()
    url = (os.getenv("SOLACE_BROKER_URL") or "").strip()

    if not host and url:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip()
        if not port_s and parsed.port:
            # wss://host:443 is common for Web UI; native MQTT TLS on Solace Cloud is usually 8883
            if parsed.scheme in ("wss", "https") and parsed.port == 443:
                port_s = "8883"
            else:
                port_s = str(parsed.port)
        if not port_s:
            port_s = "8883" if parsed.scheme in ("wss", "https", "mqtts", "tls") else "1883"

    if not user:
        user = (os.getenv("SOLACE_BROKER_USERNAME") or "").strip()
    if not password:
        password = (os.getenv("SOLACE_BROKER_PASSWORD") or "").strip()

    return (
        host or "YOUR_BROKER.messaging.solace.cloud",
        int(port_s or "8883"),
        user or "YOUR_USERNAME",
        password or "YOUR_PASSWORD",
    )


# ── Broker Configuration ─────────────────────────────────────────────────────
BROKER_HOST, BROKER_PORT, USERNAME, PASSWORD = _resolve_broker_from_env()
USE_TLS = os.getenv("SOLACE_TLS", "true").lower() in ("true", "1", "yes")

# Placeholder values returned by _resolve_broker_from_env when env vars are missing.
# Any of these reaching a connect() call almost certainly means a missing/wrong .env.
_PLACEHOLDER_VALUES = frozenset({
    "YOUR_BROKER.messaging.solace.cloud",
    "YOUR_USERNAME",
    "YOUR_PASSWORD",
})
_broker_config_validated = False


def validate_broker_config() -> None:
    """
    Fail fast when broker env vars are missing or still placeholders. Idempotent —
    safe to call from multiple entry points. Catches the common "I forgot to source
    .env" mistake before paho buries it under a confusing TLS/DNS error.
    """
    global _broker_config_validated
    if _broker_config_validated:
        return
    problems = []
    if not BROKER_HOST or BROKER_HOST in _PLACEHOLDER_VALUES:
        problems.append(f"BROKER_HOST={BROKER_HOST!r} — set SOLACE_BROKER_URL or SOLACE_HOST")
    if not USERNAME or USERNAME in _PLACEHOLDER_VALUES:
        problems.append(f"USERNAME={USERNAME!r} — set SOLACE_BROKER_USERNAME (or SOLACE_USER)")
    if not PASSWORD or PASSWORD in _PLACEHOLDER_VALUES:
        problems.append("PASSWORD is empty or placeholder — set SOLACE_BROKER_PASSWORD (or SOLACE_PASS)")
    if problems:
        msg = "Broker configuration is incomplete:\n  - " + "\n  - ".join(problems)
        msg += "\nSource sam/.env (laptop) or deploy/aws/.env (compose) before starting the service."
        raise SystemExit(msg)
    _broker_config_validated = True

# ── Topic Namespace and Schemas ──────────────────────────────────────────────
DC_NAMESPACE = os.getenv("DC_NAMESPACE", "dc")
DC_TOPIC_VERSION = os.getenv("DC_TOPIC_VERSION", "v1")
_raw_site = (os.getenv("DC_BROKER_SITE", "Hub") or "Hub").strip()
DC_BROKER_SITE = _raw_site if re.fullmatch(r"[A-Za-z0-9_-]+", _raw_site) else "Hub"
DC_PIPELINE_MULTISITE_RAW = os.getenv("DC_PIPELINE_MULTISITE_RAW", "").lower() in (
    "1",
    "true",
    "yes",
)
DEFAULT_SITE = os.getenv("DC_DEFAULT_SITE", "dc1")
DEFAULT_ROOM = os.getenv("DC_DEFAULT_ROOM", "hall-a")

SCHEMA_RAW = f"{DC_NAMESPACE}.raw.{DC_TOPIC_VERSION}"
SCHEMA_FILTERED = f"{DC_NAMESPACE}.filtered.{DC_TOPIC_VERSION}"
SCHEMA_SKETCH = f"{DC_NAMESPACE}.sketch.{DC_TOPIC_VERSION}"
SCHEMA_EVENT = f"{DC_NAMESPACE}.event.{DC_TOPIC_VERSION}"
SCHEMA_REVISION = "1.0.0"

# Optional keys on raw ingest that are copied onto filtered/suppressed outputs
# (simulation fidelity: which signals exist in-bundle vs not modeled).
RAW_METADATA_KEYS_FOR_FILTERED = ("telemetry_availability",)


def copy_raw_metadata_to_result(payload: dict, result: dict) -> None:
    """Merge non-telemetry metadata from the raw message into the deadband result."""
    for k in RAW_METADATA_KEYS_FOR_FILTERED:
        v = payload.get(k)
        if v is not None:
            result[k] = v


def pipeline_topic_prefix() -> str:
    """MQTT prefix for this broker deployment: ``dc/<DC_BROKER_SITE>/v1``."""
    return f"{DC_NAMESPACE}/{DC_BROKER_SITE}/{DC_TOPIC_VERSION}"


_tp = pipeline_topic_prefix()

# ── Pipeline Topics ──────────────────────────────────────────────────────────
# Input from sensors (per spoke: dc/DC1/v1/raw/#; hub aggregate: dc/+/v1/raw/#)
TOPIC_SENSOR_RAW = (
    f"{DC_NAMESPACE}/+/{DC_TOPIC_VERSION}/raw/#"
    if DC_PIPELINE_MULTISITE_RAW
    else f"{_tp}/raw/#"
)

# Inter-service communication (always under this process's DC_BROKER_SITE)
TOPIC_FILTERED = f"{_tp}/pipeline/filtered"     # Deadband → Sketch
TOPIC_SKETCHED = f"{_tp}/pipeline/sketched"     # Sketch → Anomaly

# Output topics
TOPIC_SUPPRESSED = f"{_tp}/pipeline/suppressed" # Filtered out readings
TOPIC_ALERTS = f"{_tp}/pipeline/alerts"         # Legacy flat alerts
TOPIC_EVENT_BASE = f"{_tp}/event"
TOPIC_SKETCH_BASE = f"{_tp}/sketch"

# ── Processing Thresholds ────────────────────────────────────────────────────
DEADBAND_PCT = 0.02       # 2% change threshold
HEARTBEAT_SECS = 30.0     # Force forward after 30s
WINDOW_SECS = 30.0        # Rolling window for statistics
WARNING_TEMP = 58.0       # Warning zone threshold
CRITICAL_TEMP = 65.0      # Critical zone threshold

# ── Fleet Status ─────────────────────────────────────────────────────────────
FLEET_UPDATE_INTERVAL = 10.0  # Update fleet status every 10 seconds


def classify_zone(temp):
    """Classify temperature into zone."""
    if temp >= CRITICAL_TEMP:
        return "CRITICAL"
    if temp >= WARNING_TEMP:
        return "WARNING"
    return "NORMAL"


def parse_raw_topic_with_temperature(topic: str):
    """
    Parse: dc/<brokerSite>/v1/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}[ / extra… ]

    ``brokerSite`` is one segment (Hub, DC1, DC2, …) between namespace and version.

    Optional temperature in the topic (ingress normalization):

    - …/temperature/<float>  — explicit segment before the numeric value
    - …/<float> as a 7th segment after ``raw`` (site…metric + °C), e.g.
      ``…/machine-001/supply_temp_c/52.3``

    Only a trailing numeric segment is accepted when there are at least seven
    segments after ``raw`` (so ``…/supply_temp_c/52.3`` is unambiguous).

    Returns (location_meta_dict, temperature_from_topic_or_none).
    """
    parts = topic.split("/")
    raw_idx = parts.index("raw") if "raw" in parts else -1
    if raw_idx == -1:
        return {}, None
    suffix = list(parts[raw_idx + 1 :])
    if not suffix:
        return {}, None

    temp_from_topic = None

    if len(suffix) >= 2 and suffix[-2].lower() == "temperature":
        try:
            temp_from_topic = float(suffix[-1])
            suffix = suffix[:-2]
        except (TypeError, ValueError):
            temp_from_topic = None

    if temp_from_topic is None and len(suffix) >= 7:
        try:
            temp_from_topic = float(suffix[-1])
            suffix = suffix[:-1]
        except (TypeError, ValueError):
            temp_from_topic = None

    fields = ["site", "room", "row", "rack", "asset", "metric"]
    out = {}
    for idx, key in enumerate(fields):
        out[key] = suffix[idx] if idx < len(suffix) else None
    return out, temp_from_topic


def parse_raw_topic(topic: str):
    """
    Parse: dc/<brokerSite>/v1/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}
    Returns dict with available fields (missing fields become None).
    See parse_raw_topic_with_temperature for optional °C in the topic path.
    """
    meta, _temp = parse_raw_topic_with_temperature(topic)
    return meta


def build_event_topic(site: str, severity: str, event_type: str) -> str:
    return f"{TOPIC_EVENT_BASE}/{site}/{severity.lower()}/{event_type}"


def build_sketch_topic(site: str, room: str, incident_id: str) -> str:
    return f"{TOPIC_SKETCH_BASE}/{site}/{room}/{incident_id}"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def publish_checked(client, topic: str, payload, *, qos: int = 0, source: str = "pipeline") -> bool:
    """
    Wrapper around client.publish that surfaces *local* publish failures (Paho can't queue —
    e.g. disconnected, queue full, message too large). At QoS 0 there's no broker ack to wait for,
    so this is the best we can do without changing semantics. Returns True when queued, else False.
    """
    info = client.publish(topic, payload, qos=qos)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        get_logger(source).warning("PUBLISH-DROPPED rc=%s topic=%s", info.rc, topic)
        return False
    return True


def create_mqtt_client(service_name: str, userdata: dict = None):
    """Create and configure an MQTT client for a pipeline service."""
    validate_broker_config()
    if userdata is None:
        userdata = {}

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{service_name}-{int(time.time())}",
        protocol=mqtt.MQTTv5,
        userdata=userdata
    )
    client.username_pw_set(USERNAME, PASSWORD)
    
    if USE_TLS:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    
    return client


def print_service_banner(service_name: str, subscribe_topic: str, publish_topic: str = None):
    """Log startup banner for a pipeline service. Name kept for backward compatibility."""
    banner_log = get_logger(service_name.upper())
    banner_log.info(
        "starting | broker=%s:%s | site=%s%s | subscribe=%s%s | TLS=%s",
        BROKER_HOST, BROKER_PORT,
        DC_BROKER_SITE,
        " (multisite raw)" if DC_PIPELINE_MULTISITE_RAW else "",
        subscribe_topic,
        f" | publish={publish_topic}" if publish_topic else "",
        "enabled" if USE_TLS else "disabled",
    )
