#!/usr/bin/env python3
"""
mock_pipeline.py
================
Lightweight mock of the SAM agent pipeline for demo purposes.

Subscribes to raw sensor events, processes them through:
  1. Deadband filter (suppresses noise)
  2. Sketch generator (creates NL summary)
  3. Anomaly assessment (mock LLM response)

Publishes to the pipeline topics that the dashboard expects.

This avoids the heavy SAM/pandas dependencies while demonstrating
the full event flow.
"""

import collections
import json
import os
import random
import ssl
import time

import paho.mqtt.client as mqtt

# ── Broker config ────────────────────────────────────────────────────────────
BROKER_HOST = os.getenv("SOLACE_HOST", "YOUR_BROKER.messaging.solace.cloud")
BROKER_PORT = int(os.getenv("SOLACE_PORT", "8883"))
USERNAME = os.getenv("SOLACE_USER", "YOUR_USERNAME")
PASSWORD = os.getenv("SOLACE_PASS", "YOUR_PASSWORD")
USE_TLS = os.getenv("SOLACE_TLS", "true").lower() in ("true", "1", "yes")

# ── Topics ───────────────────────────────────────────────────────────────────
SUBSCRIBE_TOPIC = "sensors/temperature/#"
SKETCH_TOPIC = "sensors/pipeline/sketch-input"
ORCHESTRATOR_TOPIC = "sensors/pipeline/orchestrator-input"
ALERTS_TOPIC = "sensors/pipeline/alerts"

# ── Deadband state ───────────────────────────────────────────────────────────
_last_value = {}
_last_forward_ts = {}
_windows = {}

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
    
    return "forward", {
        "action": "forward",
        "sensorId": sensor_id,
        "temperature": temperature,
        "timestamp": timestamp,
        "zone": zone,
        "delta_pct": round(delta_pct_out, 4),
        "forwarded_reason": forwarded_reason,
        "window": window
    }


def generate_sketch(sensor_id, temperature, zone, delta_pct, forwarded_reason, window):
    """Generate natural language sketch"""
    win_mean = window.get("mean", temperature)
    win_min = window.get("min", temperature)
    win_max = window.get("max", temperature)
    delta_pct_pct = delta_pct * 100
    
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
    
    return {
        "sensorId": sensor_id,
        "zone": zone,
        "sketch": sketch,
        "raw_value": temperature,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window": window
    }


def generate_alert(sensor_id, zone, sketch, temperature):
    """Generate mock anomaly alert (simulating LLM response)"""
    if zone == "NORMAL":
        return None
    
    if zone == "CRITICAL":
        assessment = "alert"
        confidence = round(random.uniform(0.85, 0.98), 2)
        reasoning = (
            f"Temperature spike to {temperature:.1f}°C exceeds critical threshold. "
            f"Immediate attention required to prevent equipment damage."
        )
        action = "Dispatch technician for immediate inspection. Consider emergency shutdown if trend continues."
    else:  # WARNING
        assessment = "advisory"
        confidence = round(random.uniform(0.70, 0.85), 2)
        reasoning = (
            f"Temperature elevated to {temperature:.1f}°C, approaching warning threshold. "
            f"Monitor closely for further escalation."
        )
        action = "Increase monitoring frequency. Schedule preventive inspection within 24 hours."
    
    return {
        "sensorId": sensor_id,
        "zone": zone,
        "assessment": assessment,
        "confidence": confidence,
        "reasoning": reasoning,
        "action": action,
        "llm_mock": True
    }


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
        timestamp = payload.get("timestamp", "")
        event_type = payload.get("eventType", "")
        
        if not sensor_id or temperature is None:
            return
        
        # ── Stage 1: Deadband Filter ─────────────────────────────────────────
        action, result = apply_deadband(sensor_id, temperature, timestamp)
        
        if action == "suppress":
            print(f"[Deadband] 🔇 SUPPRESS {sensor_id} | {result['reason']}")
            return
        
        zone = result["zone"]
        zone_icon = {"NORMAL": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(zone, "⚪")
        print(f"[Deadband] {zone_icon} FORWARD {sensor_id} | {temperature:.1f}°C | zone={zone}")
        
        # Publish to sketch-input topic
        client.publish(SKETCH_TOPIC, json.dumps(result))
        
        # ── Stage 2: Sketch Generator ────────────────────────────────────────
        sketch_result = generate_sketch(
            sensor_id, temperature, zone,
            result["delta_pct"], result["forwarded_reason"], result["window"]
        )
        
        print(f"[Sketch]   ✍️  {sensor_id} | \"{sketch_result['sketch'][:60]}...\"")
        
        # Publish to orchestrator-input topic
        client.publish(ORCHESTRATOR_TOPIC, json.dumps(sketch_result))
        
        # ── Stage 3: Anomaly Assessment (zone-gated) ─────────────────────────
        if zone == "NORMAL":
            print(f"[Anomaly]  💤 SKIP LLM | {sensor_id} zone=NORMAL")
        else:
            alert = generate_alert(sensor_id, zone, sketch_result["sketch"], temperature)
            if alert:
                alert_icon = "🚨" if zone == "CRITICAL" else "⚡"
                print(f"[Anomaly]  {alert_icon} ALERT | {sensor_id} | {alert['assessment']} | conf={alert['confidence']}")
                client.publish(ALERTS_TOPIC, json.dumps(alert))
        
        print()
        
    except Exception as e:
        print(f"[Pipeline] ❌ Error processing message: {e}")


def main():
    userdata = {"connected": False}
    
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"mock-pipeline-{int(time.time())}",
        protocol=mqtt.MQTTv5,
        userdata=userdata
    )
    client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    
    if USE_TLS:
        print(f"[Pipeline] 🔒 TLS enabled")
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    
    print(f"\n{'='*60}")
    print("  MOCK PIPELINE  |  Deadband → Sketch → Anomaly")
    print(f"{'='*60}")
    print(f"  Broker : {BROKER_HOST}:{BROKER_PORT}")
    print(f"  Input  : {SUBSCRIBE_TOPIC}")
    print(f"  Output : {SKETCH_TOPIC}")
    print(f"           {ORCHESTRATOR_TOPIC}")
    print(f"           {ALERTS_TOPIC}")
    print(f"{'='*60}\n")
    
    try:
        client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[Pipeline] Stopped by user.")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
