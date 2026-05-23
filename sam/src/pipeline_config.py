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
  DOMAIN_METRICS_PATH - JSON metric rules (deadband %, zones); see configs/domains/hvac/metrics.json
  POINT_ID_SEPARATOR  - Join asset + metric into pointId (default ":")
  ENABLE_RAW_BUNDLE   - Accept dc.raw.bundle.v1 multi-metric payloads (default: true)
"""

import json
import logging
import os
import re
import ssl
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse


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
SCHEMA_RAW_BUNDLE = f"{DC_NAMESPACE}.raw.bundle.{DC_TOPIC_VERSION}"
SCHEMA_FILTERED = f"{DC_NAMESPACE}.filtered.{DC_TOPIC_VERSION}"
SCHEMA_SKETCH = f"{DC_NAMESPACE}.sketch.{DC_TOPIC_VERSION}"
SCHEMA_EVENT = f"{DC_NAMESPACE}.event.{DC_TOPIC_VERSION}"
SCHEMA_REVISION = "1.0.0"

# Telemetry point identity (asset + metric)
POINT_ID_SEPARATOR = os.getenv("POINT_ID_SEPARATOR", ":")
DEFAULT_METRIC_ID = "supply_temp_c"
BUNDLE_TOPIC_METRIC = "_bundle"
ENABLE_RAW_BUNDLE = os.getenv("ENABLE_RAW_BUNDLE", "true").lower() in ("true", "1", "yes")

_metrics_config: Optional[Dict[str, Any]] = None

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

# ── Processing Thresholds (defaults; per-metric overrides in metrics.json) ───
DEADBAND_PCT = 0.02       # 2% change threshold
HEARTBEAT_SECS = 30.0     # Force forward after 30s
WINDOW_SECS = 30.0        # Rolling window for statistics
WARNING_TEMP = 58.0       # Legacy fallback for supply_temp_c
CRITICAL_TEMP = 65.0      # Legacy fallback for supply_temp_c

# ── Fleet Status ─────────────────────────────────────────────────────────────
FLEET_UPDATE_INTERVAL = 10.0  # Update fleet status every 10 seconds


def _default_metrics_path() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "configs", "domains", "hvac", "metrics.json")
    )


def load_metrics_config(force_reload: bool = False) -> Dict[str, Any]:
    """Load domain metric rules (deadband %, zones). Cached after first read."""
    global _metrics_config
    if _metrics_config is not None and not force_reload:
        return _metrics_config
    path = (os.getenv("DOMAIN_METRICS_PATH") or "").strip() or _default_metrics_path()
    with open(path, encoding="utf-8") as fh:
        _metrics_config = json.load(fh)
    return _metrics_config


def point_id_separator() -> str:
    return str(load_metrics_config().get("point_id_separator", POINT_ID_SEPARATOR))


def make_point_id(asset_id: str, metric_id: str) -> str:
    """Canonical telemetry point key: ``{asset}{sep}{metric}``."""
    return f"{asset_id}{point_id_separator()}{metric_id}"


def resolve_point_id(
    payload: Optional[Dict[str, Any]],
    topic_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve (point_id, asset_id, metric_id) from MQTT payload and/or parsed topic.

    Priority: pointId → asset+metric → legacy sensorId → asset-only (default metric).
    Returns (None, None, None) for bundle topics or missing identity.
    """
    payload = payload or {}
    topic_meta = topic_meta or {}
    default_metric = str(
        load_metrics_config().get("default_metric_id", DEFAULT_METRIC_ID)
    )

    explicit_point = (payload.get("pointId") or "").strip()
    asset = (payload.get("asset") or topic_meta.get("asset") or "").strip() or None
    metric = (payload.get("metric") or topic_meta.get("metric") or "").strip() or None

    if metric == BUNDLE_TOPIC_METRIC:
        return None, asset, metric

    if explicit_point:
        if asset and metric:
            return explicit_point, asset, metric
        sep = point_id_separator()
        if sep in explicit_point:
            a, m = explicit_point.split(sep, 1)
            return explicit_point, a, m
        return explicit_point, asset or explicit_point, metric or default_metric

    if asset and metric:
        return make_point_id(asset, metric), asset, metric

    legacy = (payload.get("sensorId") or "").strip()
    if legacy:
        sep = point_id_separator()
        if sep in legacy and not metric and not (payload.get("asset") or asset):
            a, m = legacy.split(sep, 1)
            return legacy, a, m
        legacy_asset = (
            (payload.get("machineId") or payload.get("asset") or asset or legacy).strip()
        )
        legacy_metric = metric or default_metric
        return legacy, legacy_asset, legacy_metric

    if asset:
        m = metric or default_metric
        return make_point_id(asset, m), asset, m

    return None, None, None


def observation_value(
    payload: Optional[Dict[str, Any]],
    topic_value: Optional[float] = None,
) -> Optional[float]:
    """Scalar reading from payload or optional value embedded in the topic path."""
    if topic_value is not None:
        return float(topic_value)
    payload = payload or {}
    for key in ("value", "temperature"):
        if key in payload and payload[key] is not None:
            return float(payload[key])
    return None


def is_bundle_payload(payload: Optional[Dict[str, Any]]) -> bool:
    """True when payload is a multi-metric gateway snapshot (legacy JSON ingress)."""
    if not ENABLE_RAW_BUNDLE:
        return False
    payload = payload or {}
    if payload.get("schema") == SCHEMA_RAW_BUNDLE:
        return True
    readings = payload.get("readings")
    return isinstance(readings, list) and bool(readings) and bool(
        (payload.get("asset") or "").strip()
    )


def expand_bundle_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Expand ``dc.raw.bundle.v1`` into per-point payloads shaped like ``dc.raw.v1``.

    Each reading inherits location fields and ``ts`` from the bundle envelope.
    """
    asset = (payload.get("asset") or "").strip()
    if not asset:
        return []

    ts = payload.get("ts") or payload.get("timestamp") or now_utc_iso()
    base = {
        "schema": SCHEMA_RAW,
        "schemaRevision": payload.get("schemaRevision", SCHEMA_REVISION),
        "ts": ts,
        "timestamp": ts,
        "site": payload.get("site"),
        "room": payload.get("room"),
        "row": payload.get("row"),
        "rack": payload.get("rack"),
        "asset": asset,
        "sourceProtocol": payload.get("sourceProtocol"),
    }
    if payload.get("telemetry_availability") is not None:
        base["telemetry_availability"] = payload["telemetry_availability"]

    out: List[Dict[str, Any]] = []
    for reading in payload.get("readings") or []:
        if not isinstance(reading, dict):
            continue
        metric = (reading.get("metric") or "").strip()
        if not metric or reading.get("value") is None:
            continue
        point = dict(base)
        point["metric"] = metric
        point["value"] = reading["value"]
        point["unit"] = reading.get("unit")
        point["quality"] = reading.get("quality")
        point["pointId"] = make_point_id(asset, metric)
        point["sensorId"] = point["pointId"]
        point["temperature"] = reading["value"]
        out.append(point)
    return out


def _metric_rule(metric_id: Optional[str]) -> Dict[str, Any]:
    mid = metric_id or load_metrics_config().get("default_metric_id", DEFAULT_METRIC_ID)
    return load_metrics_config().get("metrics", {}).get(mid, {})


def deadband_pct_for(metric_id: Optional[str] = None) -> float:
    rule = _metric_rule(metric_id)
    return float(rule.get("deadband_pct", DEADBAND_PCT))


def heartbeat_secs_for(metric_id: Optional[str] = None) -> float:
    rule = _metric_rule(metric_id)
    return float(rule.get("heartbeat_secs", HEARTBEAT_SECS))


def window_secs_for(metric_id: Optional[str] = None) -> float:
    rule = _metric_rule(metric_id)
    return float(rule.get("window_secs", WINDOW_SECS))


def classify_zone(value: float, metric_id: Optional[str] = None) -> str:
    """
    Classify a scalar reading into NORMAL | WARNING | CRITICAL using per-metric rules.

    When ``metric_id`` is omitted, uses ``default_metric_id`` from metrics.json
    (supply_temp_c). Unknown metrics fall back to legacy WARNING_TEMP / CRITICAL_TEMP.
    """
    mid = metric_id or load_metrics_config().get("default_metric_id", DEFAULT_METRIC_ID)
    zones = _metric_rule(mid).get("zones") or []
    severity_rank = {"CRITICAL": 2, "WARNING": 1, "NORMAL": 0}
    matched = "NORMAL"
    best_rank = 0
    for zone in zones:
        if zone.get("op") != "gte":
            continue
        threshold = zone.get("value")
        if threshold is None:
            continue
        if value >= float(threshold):
            name = str(zone.get("name", "NORMAL"))
            rank = severity_rank.get(name, 0)
            if rank > best_rank:
                best_rank = rank
                matched = name
    if zones:
        return matched
    if mid == DEFAULT_METRIC_ID or mid == "supply_temp_c":
        if value >= CRITICAL_TEMP:
            return "CRITICAL"
        if value >= WARNING_TEMP:
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
    import paho.mqtt.client as mqtt

    info = client.publish(topic, payload, qos=qos)
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        get_logger(source).warning("PUBLISH-DROPPED rc=%s topic=%s", info.rc, topic)
        return False
    return True


def create_mqtt_client(service_name: str, userdata: dict = None):
    """Create and configure an MQTT client for a pipeline service."""
    validate_broker_config()
    import paho.mqtt.client as mqtt

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
