#!/usr/bin/env python3
"""
mock_pipeline.py
================
COMBINED pipeline runner (all stages in one process).

For microservices deployment, use the separate services instead:
  - deadband_service.py  → sensors/pipeline/filtered
  - sketch_service.py    → sensors/pipeline/sketched  
  - anomaly_service.py   → sensors/pipeline/alerts

This combined version is useful for:
  - Local development/debugging
  - Simple deployments where separate scaling isn't needed
  - Demo purposes

Subscribes to raw sensor events, processes them through:
  1. Deadband filter (suppresses noise)
  2. Sketch generator (creates NL summary)
  3. Anomaly detector (rule-based alerts)
  4. Fleet status tracker (aggregate health)

All results are written to SQLite for SAM agents to query.

Architecture:
  MQTT → Pipeline → SQLite → SAM Agents (query via tools)
"""

import collections
import json
import os
import ssl
import time
from datetime import datetime

import paho.mqtt.client as mqtt

# Import our database module
import sensor_db

# Import Slack notifier for critical alerts
try:
    import slack_notifier
    SLACK_ENABLED = True
except ImportError:
    SLACK_ENABLED = False
    print("[Pipeline] Slack notifier not available")

# ── Broker config ────────────────────────────────────────────────────────────
BROKER_HOST = os.getenv("SOLACE_HOST", "YOUR_BROKER.messaging.solace.cloud")
BROKER_PORT = int(os.getenv("SOLACE_PORT", "8883"))
USERNAME = os.getenv("SOLACE_USER", "YOUR_USERNAME")
PASSWORD = os.getenv("SOLACE_PASS", "YOUR_PASSWORD")
USE_TLS = os.getenv("SOLACE_TLS", "true").lower() in ("true", "1", "yes")

# ── Topics ───────────────────────────────────────────────────────────────────
SUBSCRIBE_TOPIC = "sensors/temperature/#"
SUPPRESSED_TOPIC = "sensors/pipeline/suppressed"
SKETCH_TOPIC = "sensors/pipeline/sketch-input"
ORCHESTRATOR_TOPIC = "sensors/pipeline/orchestrator-input"
ALERTS_TOPIC = "sensors/pipeline/alerts"

# ── Deadband state ───────────────────────────────────────────────────────────
_last_value = {}
_last_forward_ts = {}
_windows = {}

# ── Fleet tracking state ─────────────────────────────────────────────────────
_sensor_zones = {}  # sensor_id -> current zone
_last_fleet_update = 0
FLEET_UPDATE_INTERVAL = 10.0  # Update fleet status every 10 seconds

# ── Thresholds ───────────────────────────────────────────────────────────────
DEADBAND_PCT = 0.02      # 2% change threshold
HEARTBEAT_SECS = 30.0
WINDOW_SECS = 30.0
WARNING_TEMP = 58.0
CRITICAL_TEMP = 65.0


def classify_zone(temp):
    if temp >= CRITICAL_TEMP:
        return "CRITICAL"
    if temp >= WARNING_TEMP:
        return "WARNING"
    return "NORMAL"


def classify_severity(zone, delta_pct, temperature):
    """Determine alert severity based on zone and context."""
    if zone == "CRITICAL":
        if temperature >= 70.0:
            return "CRITICAL"
        return "HIGH"
    elif zone == "WARNING":
        if delta_pct > 0.10:  # 10% jump
            return "MEDIUM"
        return "LOW"
    return None


def get_alert_type(zone, delta_pct, forwarded_reason):
    """Determine the type of alert."""
    if delta_pct > 0.30:
        return "SPIKE"
    if forwarded_reason == "heartbeat" and zone != "NORMAL":
        return "SUSTAINED_WARNING"
    if zone == "CRITICAL":
        return "THRESHOLD_BREACH"
    return "ELEVATED_READING"


def get_window_stats(sensor_id):
    if sensor_id not in _windows or not _windows[sensor_id]:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "count": 0}
    vals = [v for _, v in _windows[sensor_id]]
    return {
        "mean": round(sum(vals) / len(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "count": len(vals)
    }


def calculate_trend(sensor_id):
    """Calculate temperature trend from window data."""
    if sensor_id not in _windows or len(_windows[sensor_id]) < 3:
        return "STABLE"
    
    vals = [v for _, v in _windows[sensor_id]]
    recent = vals[-3:]
    
    if all(recent[i] < recent[i+1] for i in range(len(recent)-1)):
        return "RISING"
    elif all(recent[i] > recent[i+1] for i in range(len(recent)-1)):
        return "FALLING"
    return "STABLE"


def apply_deadband(sensor_id, temperature, timestamp):
    """Apply deadband filter - returns (action, result_dict)"""
    now = time.time()
    
    # Update window
    if sensor_id not in _windows:
        _windows[sensor_id] = collections.deque(maxlen=50)
    _windows[sensor_id].append((now, temperature))
    
    # Prune old entries
    cutoff = now - WINDOW_SECS
    while _windows[sensor_id] and _windows[sensor_id][0][0] < cutoff:
        _windows[sensor_id].popleft()
    
    prev_val = _last_value.get(sensor_id)
    last_fwd = _last_forward_ts.get(sensor_id, 0)
    
    if prev_val is not None:
        delta_pct = abs(temperature - prev_val) / max(abs(prev_val), 1.0)
        heartbeat_due = (now - last_fwd) >= HEARTBEAT_SECS
        
        if delta_pct < DEADBAND_PCT and not heartbeat_due:
            return "suppress", {
                "action": "suppress",
                "sensorId": sensor_id,
                "reason": f"delta {delta_pct*100:.1f}% < {DEADBAND_PCT*100}%"
            }
        
        forwarded_reason = "heartbeat" if delta_pct < DEADBAND_PCT else "delta"
        delta_pct_out = delta_pct
    else:
        forwarded_reason = "first-reading"
        delta_pct_out = 0.0
    
    _last_value[sensor_id] = temperature
    _last_forward_ts[sensor_id] = now
    
    zone = classify_zone(temperature)
    window = get_window_stats(sensor_id)
    trend = calculate_trend(sensor_id)
    
    # Track sensor zone for fleet status
    _sensor_zones[sensor_id] = zone
    
    return "forward", {
        "action": "forward",
        "sensorId": sensor_id,
        "temperature": temperature,
        "timestamp": timestamp,
        "zone": zone,
        "delta_pct": round(delta_pct_out, 4),
        "forwarded_reason": forwarded_reason,
        "window": window,
        "trend": trend
    }


def generate_sketch(sensor_id, temperature, zone, delta_pct, forwarded_reason, window, trend):
    """Generate natural language sketch"""
    win_mean = window.get("mean", temperature)
    win_min = window.get("min", temperature)
    win_max = window.get("max", temperature)
    delta_pct_pct = delta_pct * 100
    
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    if forwarded_reason == "heartbeat":
        sketch = (
            f"[HEARTBEAT] {sensor_id} stable at ~{win_mean:.1f}°C "
            f"(range {win_min:.1f}–{win_max:.1f}°C) over last 30s. "
            f"No significant change. Zone: {zone}."
        )
    else:
        move = "spike" if temperature > win_mean else "drop"
        sketch = (
            f"{sensor_id} recorded a {delta_pct_pct:.1f}% {move} to "
            f"{temperature:.1f}°C. 30s window: mean {win_mean:.1f}°C, "
            f"range [{win_min:.1f}–{win_max:.1f}°C]. Zone: {zone}."
        )
        if zone == "CRITICAL":
            sketch += " ⚠️ ANOMALY — immediate review required."
        elif zone == "WARNING":
            sketch += " ⚡ Elevated — monitoring advised."
    
    # Write to database
    sensor_db.insert_sketch(
        sensor_id=sensor_id,
        temperature=temperature,
        zone=zone,
        sketch=sketch,
        timestamp=timestamp,
        trend=trend,
        window_avg=win_mean,
        window_min=win_min,
        window_max=win_max
    )
    
    return {
        "sensorId": sensor_id,
        "zone": zone,
        "sketch": sketch,
        "raw_value": temperature,
        "timestamp": timestamp,
        "window": window,
        "trend": trend
    }


def generate_alert(sensor_id, zone, temperature, delta_pct, forwarded_reason):
    """Generate alert if conditions warrant (deterministic - no LLM)"""
    if zone == "NORMAL":
        return None
    
    severity = classify_severity(zone, delta_pct, temperature)
    if not severity:
        return None
    
    alert_type = get_alert_type(zone, delta_pct, forwarded_reason)
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    # Generate description based on alert type
    if alert_type == "SPIKE":
        description = (
            f"Temperature spike detected: {sensor_id} jumped {delta_pct*100:.1f}% to "
            f"{temperature:.1f}°C. Immediate investigation recommended."
        )
    elif alert_type == "THRESHOLD_BREACH":
        description = (
            f"Critical threshold breached: {sensor_id} at {temperature:.1f}°C "
            f"exceeds {CRITICAL_TEMP}°C limit. Emergency protocol advised."
        )
    elif alert_type == "SUSTAINED_WARNING":
        description = (
            f"Sustained warning condition: {sensor_id} remains elevated at "
            f"{temperature:.1f}°C. Monitor for further escalation."
        )
    else:
        description = (
            f"Elevated reading: {sensor_id} at {temperature:.1f}°C "
            f"in {zone} zone. Continue monitoring."
        )
    
    # Write to database
    sensor_db.insert_alert(
        sensor_id=sensor_id,
        temperature=temperature,
        zone=zone,
        severity=severity,
        alert_type=alert_type,
        description=description,
        timestamp=timestamp
    )
    
    # Send Slack notification for CRITICAL/HIGH severity
    if SLACK_ENABLED and severity in ["CRITICAL", "HIGH"]:
        slack_notifier.send_critical_alert(
            sensor_id=sensor_id,
            temperature=temperature,
            description=description,
            alert_type=alert_type,
            severity=severity,
            timestamp=timestamp
        )
    
    return {
        "sensorId": sensor_id,
        "zone": zone,
        "severity": severity,
        "alert_type": alert_type,
        "description": description,
        "temperature": temperature,
        "timestamp": timestamp
    }


def update_fleet_status():
    """Update fleet status in database (called periodically)."""
    global _last_fleet_update
    
    now = time.time()
    if now - _last_fleet_update < FLEET_UPDATE_INTERVAL:
        return
    
    _last_fleet_update = now
    
    active_sensors = len(_sensor_zones)
    if active_sensors == 0:
        return
    
    warning_count = sum(1 for z in _sensor_zones.values() if z == "WARNING")
    critical_count = sum(1 for z in _sensor_zones.values() if z == "CRITICAL")
    
    # Determine fleet status
    if critical_count > 0:
        if critical_count >= active_sensors * 0.5:
            fleet_status = "FLEET_CRITICAL"
            notes = f"Multiple sensors in critical state ({critical_count}/{active_sensors})"
            correlation = True
        else:
            fleet_status = "CRITICAL"
            notes = f"{critical_count} sensor(s) in critical state"
            correlation = False
    elif warning_count > 0:
        if warning_count >= active_sensors * 0.5:
            fleet_status = "ELEVATED"
            notes = f"Multiple sensors in warning state ({warning_count}/{active_sensors})"
            correlation = True
        else:
            fleet_status = "WARNING"
            notes = f"{warning_count} sensor(s) in warning state"
            correlation = False
    else:
        fleet_status = "NOMINAL"
        notes = "All sensors operating normally"
        correlation = False
    
    timestamp = datetime.utcnow().isoformat() + "Z"
    
    sensor_db.insert_fleet_status(
        active_sensors=active_sensors,
        sensors_in_warning=warning_count,
        sensors_in_critical=critical_count,
        fleet_status=fleet_status,
        timestamp=timestamp,
        correlation_detected=correlation,
        notes=notes
    )
    
    # Send Slack notification for fleet-wide critical events
    if SLACK_ENABLED and fleet_status in ["FLEET_CRITICAL", "CRITICAL"]:
        slack_notifier.send_fleet_alert(
            fleet_status=fleet_status,
            active_sensors=active_sensors,
            critical_count=critical_count,
            warning_count=warning_count,
            notes=notes
        )
    
    status_icon = {
        "NOMINAL": "🟢",
        "WARNING": "🟡", 
        "ELEVATED": "🟠",
        "CRITICAL": "🔴",
        "FLEET_CRITICAL": "🔴🔴"
    }.get(fleet_status, "⚪")
    
    print(f"[Fleet]    {status_icon} {fleet_status} | {active_sensors} sensors | {warning_count}W {critical_count}C")


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[Pipeline] ✅ Connected to {BROKER_HOST}")
        client.subscribe(SUBSCRIBE_TOPIC)
        print(f"[Pipeline] 📡 Subscribed to {SUBSCRIBE_TOPIC}")
        userdata["connected"] = True
    else:
        print(f"[Pipeline] ❌ Connection failed (rc={reason_code})")


def on_message(client, userdata, msg):
    """Process incoming sensor message through the pipeline"""
    try:
        payload = json.loads(msg.payload.decode())
        sensor_id = payload.get("sensorId")
        temperature = payload.get("temperature")
        timestamp = payload.get("timestamp", datetime.utcnow().isoformat() + "Z")
        
        if not sensor_id or temperature is None:
            return
        
        # ── Stage 1: Deadband Filter ─────────────────────────────────────────
        action, result = apply_deadband(sensor_id, temperature, timestamp)
        
        if action == "suppress":
            print(f"[Deadband] 🔇 SUPPRESS {sensor_id} | {result['reason']}")
            client.publish(SUPPRESSED_TOPIC, json.dumps(result))
            return
        
        zone = result["zone"]
        zone_icon = {"NORMAL": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(zone, "⚪")
        print(f"[Deadband] {zone_icon} FORWARD {sensor_id} | {temperature:.1f}°C | zone={zone}")
        
        # Write reading to database (passed deadband)
        sensor_db.insert_reading(
            sensor_id=sensor_id,
            temperature=temperature,
            timestamp=timestamp,
            delta_percent=result["delta_pct"]
        )
        
        # Publish to sketch-input topic (for dashboard)
        client.publish(SKETCH_TOPIC, json.dumps(result))
        
        # ── Stage 2: Sketch Generator ────────────────────────────────────────
        sketch_result = generate_sketch(
            sensor_id, temperature, zone,
            result["delta_pct"], result["forwarded_reason"], 
            result["window"], result["trend"]
        )
        
        print(f"[Sketch]   ✍️  {sensor_id} | \"{sketch_result['sketch'][:60]}...\"")
        
        # Publish to orchestrator-input topic (for dashboard)
        client.publish(ORCHESTRATOR_TOPIC, json.dumps(sketch_result))
        
        # ── Stage 3: Anomaly Detection (deterministic - no LLM) ──────────────
        if zone == "NORMAL":
            print(f"[Anomaly]  💤 SKIP | {sensor_id} zone=NORMAL")
        else:
            alert = generate_alert(
                sensor_id, zone, temperature, 
                result["delta_pct"], result["forwarded_reason"]
            )
            if alert:
                alert_icon = "🚨" if alert["severity"] in ["CRITICAL", "HIGH"] else "⚡"
                print(f"[Anomaly]  {alert_icon} {alert['severity']} | {sensor_id} | {alert['alert_type']}")
                client.publish(ALERTS_TOPIC, json.dumps(alert))
        
        # ── Stage 4: Fleet Status Update ─────────────────────────────────────
        update_fleet_status()
        
        print()
        
    except Exception as e:
        print(f"[Pipeline] ❌ Error processing message: {e}")
        import traceback
        traceback.print_exc()


def main():
    # Initialize the database
    print("[Pipeline] 📦 Initializing SQLite database...")
    sensor_db.init_database()
    
    userdata = {"connected": False}
    
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"pipeline-{int(time.time())}",
        protocol=mqtt.MQTTv5,
        userdata=userdata
    )
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    
    if USE_TLS:
        print(f"[Pipeline] 🔒 TLS enabled")
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    
    print(f"\n{'='*65}")
    print("  DATA PLANE PIPELINE (COMBINED)")
    print("  Deadband → Sketch → Anomaly → SQLite")
    print(f"{'='*65}")
    print(f"  Broker   : {BROKER_HOST}:{BROKER_PORT}")
    print(f"  Input    : {SUBSCRIBE_TOPIC}")
    print(f"  Database : {sensor_db.get_db_path()}")
    print(f"{'='*65}")
    print("  For microservices, run separately:")
    print("    python deadband_service.py")
    print("    python sketch_service.py")
    print("    python anomaly_service.py")
    print(f"{'='*65}\n")
    
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[Pipeline] Stopped by user.")
        print(f"[Pipeline] Final stats: {sensor_db.get_statistics()}")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
