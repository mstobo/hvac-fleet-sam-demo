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
"""

import os
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import paho.mqtt.client as mqtt


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

# ── Topic Namespace and Schemas ──────────────────────────────────────────────
DC_NAMESPACE = os.getenv("DC_NAMESPACE", "dc")
DC_TOPIC_VERSION = os.getenv("DC_TOPIC_VERSION", "v1")
DEFAULT_SITE = os.getenv("DC_DEFAULT_SITE", "dc1")
DEFAULT_ROOM = os.getenv("DC_DEFAULT_ROOM", "hall-a")

SCHEMA_RAW = f"{DC_NAMESPACE}.raw.{DC_TOPIC_VERSION}"
SCHEMA_FILTERED = f"{DC_NAMESPACE}.filtered.{DC_TOPIC_VERSION}"
SCHEMA_SKETCH = f"{DC_NAMESPACE}.sketch.{DC_TOPIC_VERSION}"
SCHEMA_EVENT = f"{DC_NAMESPACE}.event.{DC_TOPIC_VERSION}"
SCHEMA_REVISION = "1.0.0"

# ── Pipeline Topics ──────────────────────────────────────────────────────────
# Input from sensors
TOPIC_SENSOR_RAW = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/raw/#"

# Inter-service communication
TOPIC_FILTERED = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/pipeline/filtered"     # Deadband → Sketch
TOPIC_SKETCHED = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/pipeline/sketched"     # Sketch → Anomaly

# Output topics
TOPIC_SUPPRESSED = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/pipeline/suppressed" # Filtered out readings
TOPIC_ALERTS = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/pipeline/alerts"         # Legacy flat alerts
TOPIC_EVENT_BASE = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/event"
TOPIC_SKETCH_BASE = f"{DC_NAMESPACE}/{DC_TOPIC_VERSION}/sketch"

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


def parse_raw_topic(topic: str):
    """
    Parse: dc/v1/raw/{site}/{room}/{row}/{rack}/{asset}/{metric}
    Returns dict with available fields (missing fields become None).
    """
    parts = topic.split("/")
    raw_idx = parts.index("raw") if "raw" in parts else -1
    if raw_idx == -1:
        return {}
    suffix = parts[raw_idx + 1 :]
    fields = ["site", "room", "row", "rack", "asset", "metric"]
    out = {}
    for idx, key in enumerate(fields):
        out[key] = suffix[idx] if idx < len(suffix) else None
    return out


def build_event_topic(site: str, severity: str, event_type: str) -> str:
    return f"{TOPIC_EVENT_BASE}/{site}/{severity.lower()}/{event_type}"


def build_sketch_topic(site: str, room: str, incident_id: str) -> str:
    return f"{TOPIC_SKETCH_BASE}/{site}/{room}/{incident_id}"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def create_mqtt_client(service_name: str, userdata: dict = None):
    """Create and configure an MQTT client for a pipeline service."""
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
    """Print startup banner for a pipeline service."""
    print(f"\n{'='*65}")
    print(f"  {service_name.upper()} SERVICE")
    print(f"{'='*65}")
    print(f"  Broker    : {BROKER_HOST}:{BROKER_PORT}")
    print(f"  Subscribe : {subscribe_topic}")
    if publish_topic:
        print(f"  Publish   : {publish_topic}")
    print(f"  TLS       : {'Enabled' if USE_TLS else 'Disabled'}")
    print(f"{'='*65}\n")
