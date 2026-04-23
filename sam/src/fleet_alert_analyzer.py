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

This module is called by anomaly_service.py when fleet status changes.
It queries SAM's fleet_query_agent and pushes results to Slack.
"""

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

import requests

# Optional Slack integration
try:
    import slack_notifier
    SLACK_ENABLED = True
except ImportError:
    SLACK_ENABLED = False

# ── Configuration ────────────────────────────────────────────────────────────
DEBOUNCE_SECONDS = 60.0        # Wait this long after first critical
RATE_LIMIT_SECONDS = 300.0     # Min time between LLM analyses (5 minutes)
SAM_API_URL = os.getenv("SAM_API_URL", "http://localhost:5001")
ENABLE_AUTO_ANALYSIS = os.getenv("ENABLE_AUTO_ANALYSIS", "true").lower() in ("true", "1", "yes")

# ── State ────────────────────────────────────────────────────────────────────
_last_analysis_time = 0.0
_pending_analysis = False
_pending_timer: Optional[threading.Timer] = None
_collected_criticals = []
_lock = threading.Lock()


def _should_analyze() -> bool:
    """Check if we should trigger analysis (rate limit check)."""
    now = time.time()
    if now - _last_analysis_time < RATE_LIMIT_SECONDS:
        remaining = RATE_LIMIT_SECONDS - (now - _last_analysis_time)
        print(f"[AutoAnalysis] ⏳ Rate limited. Next analysis allowed in {remaining:.0f}s")
        return False
    return True


def _build_analysis_prompt(criticals: list) -> str:
    """Build a prompt for the LLM to analyze the fleet critical event."""
    sensor_list = ", ".join(set(c.get("sensor_id", "unknown") for c in criticals))
    temps = [c.get("temperature", 0) for c in criticals if c.get("temperature")]
    avg_temp = sum(temps) / len(temps) if temps else 0
    
    return f"""FLEET CRITICAL EVENT - Automatic Analysis Request

Multiple sensors have entered critical state simultaneously.

Affected sensors: {sensor_list}
Number of critical readings: {len(criticals)}
Average temperature: {avg_temp:.1f}°C
Time window: Last 60 seconds

Please analyze:
1. What patterns do you see across these sensors?
2. Is this likely a correlated event (shared cause) or independent failures?
3. What are the most likely root causes?
4. What immediate actions should operators take?

Use get_sketches() and get_recent_alerts() to gather context before responding."""


def _execute_analysis():
    """Execute the LLM analysis (called after debounce period)."""
    global _last_analysis_time, _pending_analysis, _collected_criticals
    
    with _lock:
        if not _collected_criticals:
            print("[AutoAnalysis] No criticals collected, skipping analysis")
            _pending_analysis = False
            return
        
        criticals = _collected_criticals.copy()
        _collected_criticals = []
        _pending_analysis = False
    
    print(f"\n[AutoAnalysis] 🤖 Triggering LLM analysis for {len(criticals)} critical events...")
    
    prompt = _build_analysis_prompt(criticals)
    
    try:
        # Try to query SAM via REST API
        response = _query_sam_agent(prompt)
        
        if response:
            _last_analysis_time = time.time()
            print(f"[AutoAnalysis] ✅ Analysis complete")
            print(f"[AutoAnalysis] Response preview: {response[:200]}...")
            
            # Push to Slack if enabled
            if SLACK_ENABLED:
                _push_to_slack(response, len(criticals))
        else:
            print("[AutoAnalysis] ❌ No response from SAM agent")
            
    except Exception as e:
        print(f"[AutoAnalysis] ❌ Error during analysis: {e}")


def _query_sam_agent(prompt: str) -> Optional[str]:
    """
    Query the SAM fleet_query_agent via REST API.
    
    SAM exposes agents via REST at /api/v1/agents/{agent_name}/chat
    """
    try:
        # SAM REST API endpoint
        url = f"{SAM_API_URL}/api/v1/request"
        
        payload = {
            "prompt": prompt,
            "stream": False
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        print(f"[AutoAnalysis] Querying SAM at {url}")
        response = requests.post(url, json=payload, headers=headers, timeout=120)
        
        if response.status_code == 200:
            result = response.json()
            return result.get("response", result.get("content", str(result)))
        else:
            print(f"[AutoAnalysis] SAM API returned {response.status_code}: {response.text[:200]}")
            return None
            
    except requests.exceptions.ConnectionError:
        print(f"[AutoAnalysis] Could not connect to SAM at {SAM_API_URL}")
        print("[AutoAnalysis] Make sure 'sam run' is running")
        return None
    except Exception as e:
        print(f"[AutoAnalysis] Error querying SAM: {e}")
        return None


def _push_to_slack(analysis: str, critical_count: int):
    """Push the LLM analysis to Slack."""
    try:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        message = f"""🤖 *Automatic Fleet Analysis*
        
*Trigger:* FLEET_CRITICAL ({critical_count} sensors)
*Time:* {timestamp}

---

{analysis}

---
_This analysis was automatically triggered by the fleet alert system._"""
        
        # Use the slack_notifier module
        slack_notifier.send_message(message)
        print("[AutoAnalysis] 📤 Analysis pushed to Slack")
        
    except Exception as e:
        print(f"[AutoAnalysis] Failed to push to Slack: {e}")


def on_fleet_critical(fleet_status: str, critical_count: int, active_sensors: int, 
                       notes: str, sensor_data: dict = None):
    """
    Called by anomaly_service when fleet status is FLEET_CRITICAL or CRITICAL.
    
    Implements debounce: First call starts a timer, subsequent calls add to the batch.
    After DEBOUNCE_SECONDS, triggers LLM analysis with all collected criticals.
    
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
            "timestamp": datetime.utcnow().isoformat(),
            **(sensor_data or {})
        })
        
        # If already pending, just add to batch
        if _pending_analysis:
            print(f"[AutoAnalysis] 📥 Added to pending batch ({len(_collected_criticals)} events)")
            return
        
        # Check rate limit before starting debounce
        if not _should_analyze():
            _collected_criticals.clear()
            return
        
        # Start debounce timer
        _pending_analysis = True
        print(f"[AutoAnalysis] ⏱️  FLEET_CRITICAL detected. Starting {DEBOUNCE_SECONDS}s debounce...")
        
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
        if _pending_analysis:
            _collected_criticals.append({
                "sensor_id": sensor_id,
                "temperature": temperature,
                "zone": zone,
                "timestamp": datetime.utcnow().isoformat()
            })


def get_status() -> dict:
    """Get current status of the auto-analyzer."""
    with _lock:
        now = time.time()
        time_since_last = now - _last_analysis_time if _last_analysis_time > 0 else None
        time_until_allowed = max(0, RATE_LIMIT_SECONDS - time_since_last) if time_since_last else 0
        
        return {
            "enabled": ENABLE_AUTO_ANALYSIS,
            "pending_analysis": _pending_analysis,
            "collected_events": len(_collected_criticals),
            "last_analysis_ago_seconds": time_since_last,
            "next_analysis_allowed_in": time_until_allowed,
            "debounce_seconds": DEBOUNCE_SECONDS,
            "rate_limit_seconds": RATE_LIMIT_SECONDS
        }


# For testing
if __name__ == "__main__":
    print("Testing fleet_alert_analyzer...")
    print(f"Status: {get_status()}")
    
    # Simulate a FLEET_CRITICAL event
    on_fleet_critical(
        fleet_status="FLEET_CRITICAL",
        critical_count=3,
        active_sensors=3,
        notes="Test event"
    )
    
    # Wait for debounce
    print(f"Waiting {DEBOUNCE_SECONDS}s for debounce...")
    time.sleep(DEBOUNCE_SECONDS + 5)
    print("Done")
