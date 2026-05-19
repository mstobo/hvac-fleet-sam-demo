#!/usr/bin/env python3
"""
anomaly_service.py
==================
Microservice 3/3: Anomaly Detector

Subscribes to: sensors/pipeline/sketched
Publishes to:  sensors/pipeline/alerts
Writes to:     SQLite (alerts, sensor_readings, fleet_status tables)

Detects anomalies using rule-based logic and generates alerts.
Also tracks fleet-wide status and sends Slack notifications for critical events.

This is the third stage of the deterministic data plane - no LLM involved.
All detection logic is deterministic threshold-based rules.
"""

import json
import os
import time

import pipeline_config as config
import sensor_db

log = config.get_logger("Anomaly")
_fleet_log = config.get_logger("Fleet")

# Optional Slack integration
try:
    import slack_notifier
    SLACK_ENABLED = True
except ImportError:
    SLACK_ENABLED = False
    log.info("Slack notifier not available")

# Optional auto-analysis on FLEET_CRITICAL
try:
    import fleet_alert_analyzer
    AUTO_ANALYSIS_ENABLED = True
except ImportError:
    AUTO_ANALYSIS_ENABLED = False
    log.info("fleet alert analyzer not available")

# ── Fleet Tracking State ─────────────────────────────────────────────────────
_sensor_zones = {}        # sensor_id -> current zone
_last_fleet_update = 0
_last_fleet_slack_status = None
_last_fleet_slack_sent_at = 0.0
FLEET_SLACK_MIN_INTERVAL_SECONDS = float(
    os.getenv("FLEET_SLACK_MIN_INTERVAL_SECONDS", "180")
)
# Share of sensors that must be in CRITICAL zone simultaneously to raise FLEET_CRITICAL
# (Slack + auto LLM analysis via fleet_alert_analyzer). Default 0.5 ⇒ 5/9. Try 0.34 ⇒ ~3/9 for demos.
FLEET_CRITICAL_FRACTION = float(os.getenv("FLEET_CRITICAL_FRACTION", "0.5"))


def _should_send_fleet_slack(fleet_status: str, now_monotonic: float) -> bool:
    """
    Send fleet Slack alerts on status transitions, otherwise throttle repeats.

    This keeps Slack actionable during turbulence while still allowing periodic
    reminders if a critical state persists.
    """
    global _last_fleet_slack_status, _last_fleet_slack_sent_at

    if _last_fleet_slack_status != fleet_status:
        _last_fleet_slack_status = fleet_status
        _last_fleet_slack_sent_at = now_monotonic
        return True

    if now_monotonic - _last_fleet_slack_sent_at >= FLEET_SLACK_MIN_INTERVAL_SECONDS:
        _last_fleet_slack_sent_at = now_monotonic
        return True

    return False


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


def generate_alert(data):
    """
    Generate alert if conditions warrant.
    
    Args:
        data: dict with sensorId, temperature, zone, delta_pct, forwarded_reason
    
    Returns:
        dict with alert details, or None if no alert needed
    """
    sensor_id = data["sensorId"]
    temperature = data["temperature"]
    zone = data["zone"]
    delta_pct = data.get("delta_pct", 0)
    forwarded_reason = data.get("forwarded_reason", "unknown")
    site = data.get("site", config.DEFAULT_SITE)
    room = data.get("room", config.DEFAULT_ROOM)
    incident_id = data.get("incidentId")
    
    # No alert for normal zone
    if zone == "NORMAL":
        return None
    
    severity = classify_severity(zone, delta_pct, temperature)
    if not severity:
        return None
    
    alert_type = get_alert_type(zone, delta_pct, forwarded_reason)
    timestamp = config.now_utc_iso()
    
    # Generate description based on alert type
    if alert_type == "SPIKE":
        description = (
            f"Temperature spike detected: {sensor_id} jumped {delta_pct*100:.1f}% to "
            f"{temperature:.1f}°C. Immediate investigation recommended."
        )
    elif alert_type == "THRESHOLD_BREACH":
        description = (
            f"Critical threshold breached: {sensor_id} at {temperature:.1f}°C "
            f"exceeds {config.CRITICAL_TEMP}°C limit. Emergency protocol advised."
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
    
    # Slack: only true CRITICAL severity (skip HIGH — CRITICAL zone but temp < 70°C)
    if SLACK_ENABLED and severity == "CRITICAL":
        slack_notifier.send_critical_alert(
            sensor_id=sensor_id,
            temperature=temperature,
            description=description,
            alert_type=alert_type,
            severity=severity,
            timestamp=timestamp
        )
    
    # Collect critical sensor data for potential auto-analysis
    if AUTO_ANALYSIS_ENABLED and zone == "CRITICAL":
        fleet_alert_analyzer.on_sensor_critical(
            sensor_id=sensor_id,
            temperature=temperature,
            zone=zone
        )
    
    return {
        "schema": config.SCHEMA_EVENT,
        "schemaRevision": config.SCHEMA_REVISION,
        "eventType": "CoolingDriftDetected" if alert_type in ["SPIKE", "ELEVATED_READING"] else "IncidentUpdated",
        "sensorId": sensor_id,
        "site": site,
        "room": room,
        "incidentId": incident_id,
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
    if now - _last_fleet_update < config.FLEET_UPDATE_INTERVAL:
        return
    
    _last_fleet_update = now
    
    active_sensors = len(_sensor_zones)
    if active_sensors == 0:
        return
    
    warning_count = sum(1 for z in _sensor_zones.values() if z == "WARNING")
    critical_count = sum(1 for z in _sensor_zones.values() if z == "CRITICAL")
    
    # Determine fleet status
    if critical_count > 0:
        if critical_count >= active_sensors * FLEET_CRITICAL_FRACTION:
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
    
    timestamp = config.now_utc_iso()
    
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
    if (
        SLACK_ENABLED
        and fleet_status in ["FLEET_CRITICAL", "CRITICAL"]
        and _should_send_fleet_slack(fleet_status, now)
    ):
        slack_notifier.send_fleet_alert(
            fleet_status=fleet_status,
            active_sensors=active_sensors,
            critical_count=critical_count,
            warning_count=warning_count,
            notes=notes
        )
    
    # Trigger auto-analysis for FLEET_CRITICAL (with debounce and rate limiting)
    if AUTO_ANALYSIS_ENABLED and fleet_status == "FLEET_CRITICAL":
        affected_critical_ids = [
            sid for sid, z in _sensor_zones.items() if z == "CRITICAL"
        ]
        fleet_alert_analyzer.on_fleet_critical(
            fleet_status=fleet_status,
            critical_count=critical_count,
            active_sensors=active_sensors,
            notes=notes,
            sensor_data={"affected_sensor_ids": affected_critical_ids},
        )
    
    status_icon = {
        "NOMINAL": "NOMINAL",
        "WARNING": "WARNING",
        "ELEVATED": "ELEVATED",
        "CRITICAL": "CRITICAL",
        "FLEET_CRITICAL": "FLEET_CRITICAL"
    }.get(fleet_status, "UNKNOWN")
    
    _fleet_log.info("%s | %d sensors | %dW %dC", status_icon, active_sensors, warning_count, critical_count)


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        log.info("connected to %s", config.BROKER_HOST)
        client.subscribe(config.TOPIC_SKETCHED)
        log.info("subscribed to %s", config.TOPIC_SKETCHED)
    else:
        log.error("connection failed rc=%s", reason_code)


def on_message(client, userdata, msg):
    """Process sketched reading for anomaly detection."""
    try:
        data = json.loads(msg.payload.decode())
        
        sensor_id = data.get("sensorId")
        temperature = data.get("temperature")
        zone = data.get("zone")
        
        if not sensor_id or temperature is None:
            return
        
        # Track sensor zone for fleet status
        _sensor_zones[sensor_id] = zone
        
        # Write reading to database
        sensor_db.insert_reading(
            sensor_id=sensor_id,
            temperature=temperature,
            timestamp=data.get("timestamp", config.now_utc_iso()),
            delta_percent=data.get("delta_pct", 0)
        )
        
        # Check for anomalies
        if zone == "NORMAL":
            log.debug("SKIP %s zone=NORMAL", sensor_id)
        else:
            alert = generate_alert(data)
            if alert:
                log.info("ALERT %s | %s | %s", alert["severity"], sensor_id, alert["alert_type"])
                alert_payload = json.dumps(alert)
                config.publish_checked(client, config.TOPIC_ALERTS, alert_payload, source="Anomaly")
                event_topic = config.build_event_topic(
                    alert.get("site", config.DEFAULT_SITE),
                    alert["severity"],
                    alert["eventType"],
                )
                config.publish_checked(client, event_topic, alert_payload, source="Anomaly")

        # Update fleet status periodically
        update_fleet_status()

    except Exception:
        log.exception("error processing message on %s", msg.topic)


def main():
    log.info("initializing SQLite database at %s", sensor_db.get_db_path())
    sensor_db.init_database()

    config.print_service_banner(
        "Anomaly Detector",
        config.TOPIC_SKETCHED,
        config.TOPIC_ALERTS
    )
    log.info("Slack: %s", "enabled" if SLACK_ENABLED else "disabled")

    client = config.create_mqtt_client("anomaly")
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(config.BROKER_HOST, config.BROKER_PORT, keepalive=60)
        client.loop_forever()
    except KeyboardInterrupt:
        log.info("stopped by user; final stats: %s", sensor_db.get_statistics())
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
