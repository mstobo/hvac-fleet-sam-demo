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

import paho.mqtt.client as mqtt

# ── Broker Configuration ─────────────────────────────────────────────────────
BROKER_HOST = os.getenv("SOLACE_HOST", "YOUR_BROKER.messaging.solace.cloud")
BROKER_PORT = int(os.getenv("SOLACE_PORT", "8883"))
USERNAME = os.getenv("SOLACE_USER", "YOUR_USERNAME")
PASSWORD = os.getenv("SOLACE_PASS", "YOUR_PASSWORD")
USE_TLS = os.getenv("SOLACE_TLS", "true").lower() in ("true", "1", "yes")

# ── Pipeline Topics ──────────────────────────────────────────────────────────
# Input from sensors
TOPIC_SENSOR_RAW = "sensors/temperature/#"

# Inter-service communication
TOPIC_FILTERED = "sensors/pipeline/filtered"      # Deadband → Sketch
TOPIC_SKETCHED = "sensors/pipeline/sketched"      # Sketch → Anomaly

# Output topics (for dashboard/monitoring)
TOPIC_SUPPRESSED = "sensors/pipeline/suppressed"  # Filtered out readings
TOPIC_ALERTS = "sensors/pipeline/alerts"          # Generated alerts

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
