#!/usr/bin/env python3
"""
fleet_query_tools.py
====================
SAM Agent tools for querying the sensor pipeline SQLite database.

These tools are used by SAM agents (with LLM) to answer user questions
about sensor status, alerts, and fleet health.

The actual data processing happens in the deterministic pipeline (mock_pipeline.py).
These tools only READ from the database - they don't process raw sensor data.
"""

import json
from datetime import datetime
from typing import Optional

# Import the database module
import sensor_db


def get_recent_alerts(minutes: int = 60, severity: str = None, limit: int = 20) -> str:
    """
    Query recent alerts from the sensor pipeline.
    
    Use this tool when the user asks about:
    - Recent alerts or warnings
    - What anomalies have occurred
    - Critical events in the system
    
    Args:
        minutes: How far back to look (default 60 minutes)
        severity: Filter by severity (LOW, MEDIUM, HIGH, CRITICAL) or None for all
        limit: Maximum number of alerts to return
    
    Returns:
        JSON string with alert details
    """
    try:
        alerts = sensor_db.get_recent_alerts(minutes=minutes, severity=severity, limit=limit)
        
        if not alerts:
            return json.dumps({
                "status": "ok",
                "message": f"No alerts found in the last {minutes} minutes" + 
                          (f" with severity {severity}" if severity else ""),
                "alerts": [],
                "count": 0
            })
        
        return json.dumps({
            "status": "ok",
            "time_window_minutes": minutes,
            "count": len(alerts),
            "alerts": alerts
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_alert_summary(minutes: int = 60) -> str:
    """
    Get a summary of all alerts in the time window.
    
    Use this tool when the user asks for:
    - An overview of alert activity
    - Alert statistics
    - Which sensors have the most issues
    
    Args:
        minutes: How far back to look (default 60 minutes)
    
    Returns:
        JSON string with alert summary statistics
    """
    try:
        summary = sensor_db.get_alert_summary(minutes=minutes)
        return json.dumps({
            "status": "ok",
            **summary
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_fleet_status() -> str:
    """
    Get the current fleet health status.
    
    Use this tool when the user asks about:
    - Overall fleet health
    - How many sensors are active
    - Current system status
    
    Returns:
        JSON string with current fleet status
    """
    try:
        status = sensor_db.get_current_fleet_status()
        return json.dumps({
            "status": "ok",
            **status
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_fleet_history(minutes: int = 60) -> str:
    """
    Get fleet status history over time.
    
    Use this tool when the user asks about:
    - How fleet status has changed
    - When issues started
    - Fleet health trends
    
    Args:
        minutes: How far back to look (default 60 minutes)
    
    Returns:
        JSON string with fleet status history
    """
    try:
        history = sensor_db.get_fleet_status_history(minutes=minutes)
        return json.dumps({
            "status": "ok",
            "time_window_minutes": minutes,
            "snapshots": history
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_sensor_details(sensor_id: str, minutes: int = 30) -> str:
    """
    Get detailed information about a specific sensor.
    
    Use this tool when the user asks about:
    - A specific sensor by name/ID
    - Temperature history for a sensor
    - What happened to sensor-001, etc.
    
    Args:
        sensor_id: The sensor identifier (e.g., "sensor-001")
        minutes: How far back to look (default 30 minutes)
    
    Returns:
        JSON string with sensor details and history
    """
    try:
        readings = sensor_db.get_sensor_history(sensor_id=sensor_id, minutes=minutes)
        sketches = sensor_db.get_recent_sketches(minutes=minutes, sensor_id=sensor_id, limit=10)
        
        # Get alerts for this sensor
        all_alerts = sensor_db.get_recent_alerts(minutes=minutes, limit=100)
        sensor_alerts = [a for a in all_alerts if a.get("sensor_id") == sensor_id]
        
        if not readings and not sketches:
            return json.dumps({
                "status": "ok",
                "message": f"No data found for sensor '{sensor_id}' in the last {minutes} minutes",
                "sensor_id": sensor_id
            })
        
        # Calculate stats from readings
        temps = [r["temperature"] for r in readings] if readings else []
        stats = {
            "reading_count": len(temps),
            "current_temp": temps[0] if temps else None,
            "avg_temp": round(sum(temps) / len(temps), 2) if temps else None,
            "min_temp": min(temps) if temps else None,
            "max_temp": max(temps) if temps else None,
        }
        
        return json.dumps({
            "status": "ok",
            "sensor_id": sensor_id,
            "time_window_minutes": minutes,
            "statistics": stats,
            "recent_readings": readings[:10],  # Last 10 readings
            "recent_sketches": sketches[:5],    # Last 5 sketches
            "alerts": sensor_alerts
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_sketches(minutes: int = 30, zone: str = None, limit: int = 20) -> str:
    """
    Get recent sketch summaries from the pipeline.
    
    Use this tool when the user asks about:
    - Recent sensor activity summaries
    - What's been happening with the sensors
    - Natural language descriptions of sensor behavior
    
    Args:
        minutes: How far back to look (default 30 minutes)
        zone: Filter by zone (NORMAL, WARNING, CRITICAL) or None for all
        limit: Maximum number of sketches to return
    
    Returns:
        JSON string with sketch summaries
    """
    try:
        sketches = sensor_db.get_recent_sketches(minutes=minutes, zone=zone, limit=limit)
        
        if not sketches:
            return json.dumps({
                "status": "ok",
                "message": f"No sketches found in the last {minutes} minutes" +
                          (f" in {zone} zone" if zone else ""),
                "sketches": [],
                "count": 0
            })
        
        return json.dumps({
            "status": "ok",
            "time_window_minutes": minutes,
            "count": len(sketches),
            "sketches": sketches
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def get_system_statistics() -> str:
    """
    Get overall system statistics.
    
    Use this tool when the user asks about:
    - How much data has been processed
    - System metrics
    - Database statistics
    
    Returns:
        JSON string with system statistics
    """
    try:
        stats = sensor_db.get_statistics()
        return json.dumps({
            "status": "ok",
            **stats
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def acknowledge_alert(alert_id: int) -> str:
    """
    Acknowledge an alert (mark it as reviewed).
    
    Use this tool when the user wants to:
    - Acknowledge an alert
    - Mark an alert as reviewed
    - Clear an alert notification
    
    Args:
        alert_id: The ID of the alert to acknowledge
    
    Returns:
        JSON string confirming the action
    """
    try:
        with sensor_db.get_connection() as conn:
            result = conn.execute(
                "UPDATE alerts SET acknowledged = TRUE WHERE id = ?",
                (alert_id,)
            )
            if result.rowcount > 0:
                return json.dumps({
                    "status": "ok",
                    "message": f"Alert {alert_id} acknowledged"
                })
            else:
                return json.dumps({
                    "status": "error",
                    "message": f"Alert {alert_id} not found"
                })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
