#!/usr/bin/env python3
"""
slack_notifier.py
=================
Simple Slack notifier for critical sensor alerts.

This module provides a lightweight way to push critical alerts to Slack
without going through SAM's LLM. For simple notifications, this is faster
and cheaper than LLM-generated messages.

For complex queries and investigations, users can still interact with
the SAM Slack gateway (@bot what caused this alert?).

Usage:
    from slack_notifier import SlackNotifier
    
    notifier = SlackNotifier()
    notifier.send_critical_alert(sensor_id, temperature, description)
"""

import json
import os
import time
from datetime import datetime
from typing import Optional
import threading

# Try to import slack_sdk, gracefully handle if not installed
try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    WebClient = None
    SlackApiError = Exception


class SlackNotifier:
    """Sends critical alerts to Slack channels."""
    
    def __init__(self):
        self.enabled = False
        self.client = None
        self.channel = os.getenv("SLACK_ALERT_CHANNEL", "#sensor-alerts")
        self.bot_token = os.getenv("SLACK_BOT_TOKEN", "")
        
        # Rate limiting: max 1 alert per sensor per 60 seconds
        self._last_alert_time = {}
        self._rate_limit_seconds = int(os.getenv("SLACK_RATE_LIMIT_SECONDS", "60"))
        
        # Deduplication: don't send same alert twice
        self._recent_alerts = set()
        self._alert_ttl = 300  # 5 minutes
        
        # Sensor-level dedupe: suppress repeated CRITICAL posts for same sensor
        # within a configurable window, regardless of minor temperature jitter.
        self._sensor_alert_dedupe = {}
        self._sensor_alert_dedupe_seconds = int(
            os.getenv("SLACK_SENSOR_DEDUPE_SECONDS", "300")
        )
        
        self._initialize()
    
    def _initialize(self):
        """Initialize Slack client if credentials are available."""
        if not SLACK_AVAILABLE:
            print("[SlackNotifier] slack_sdk not installed. Run: pip install slack_sdk")
            return
        
        if not self.bot_token or self.bot_token == "your-slack-bot-token":
            print("[SlackNotifier] SLACK_BOT_TOKEN not configured. Notifications disabled.")
            return
        
        try:
            self.client = WebClient(token=self.bot_token)
            # Test the connection
            auth_response = self.client.auth_test()
            bot_name = auth_response.get("user", "unknown")
            print(f"[SlackNotifier] Connected as @{bot_name}")
            print(f"[SlackNotifier] Alerts will be sent to {self.channel}")
            self.enabled = True
        except Exception as e:
            print(f"[SlackNotifier] Failed to connect: {e}")
            self.enabled = False
    
    def _is_rate_limited(self, sensor_id: str) -> bool:
        """Check if we've sent an alert for this sensor recently."""
        now = time.time()
        last_time = self._last_alert_time.get(sensor_id, 0)
        
        if now - last_time < self._rate_limit_seconds:
            return True
        
        self._last_alert_time[sensor_id] = now
        return False
    
    def _is_duplicate(self, alert_key: str) -> bool:
        """Check if this exact alert was sent recently."""
        if alert_key in self._recent_alerts:
            return True
        
        self._recent_alerts.add(alert_key)
        
        # Clean up old alerts in background
        def cleanup():
            time.sleep(self._alert_ttl)
            self._recent_alerts.discard(alert_key)
        
        threading.Thread(target=cleanup, daemon=True).start()
        return False

    def _is_sensor_deduped(self, sensor_id: str, severity: str) -> bool:
        """Check if the same sensor/severity was recently posted."""
        now = time.time()
        key = f"{sensor_id}:{severity}"
        last_time = self._sensor_alert_dedupe.get(key, 0.0)
        if now - last_time < self._sensor_alert_dedupe_seconds:
            return True
        self._sensor_alert_dedupe[key] = now
        return False
    
    def send_critical_alert(
        self,
        sensor_id: str,
        temperature: float,
        description: str,
        alert_type: str = "SPIKE",
        severity: str = "CRITICAL",
        timestamp: Optional[str] = None
    ) -> bool:
        """
        Send a critical alert to Slack.
        
        Returns True if sent, False if skipped (disabled, rate limited, or duplicate).
        """
        if not self.enabled:
            return False
        
        # Rate limiting
        if self._is_rate_limited(sensor_id):
            print(f"[SlackNotifier] Rate limited: {sensor_id}")
            return False
        
        # Deduplication
        alert_key = f"{sensor_id}:{temperature:.1f}:{alert_type}"
        if self._is_duplicate(alert_key):
            print(f"[SlackNotifier] Duplicate skipped: {sensor_id}")
            return False
        
        # Sensor-level dedupe to reduce repeated CRITICAL spam,
        # even if alert_type changes (e.g., THRESHOLD_BREACH -> SPIKE).
        if self._is_sensor_deduped(sensor_id, severity):
            print(f"[SlackNotifier] Sensor dedupe skipped: {sensor_id} ({severity})")
            return False
        
        if timestamp is None:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Build Slack message with blocks for rich formatting
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{severity} ALERT: {sensor_id}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Sensor:*\n{sensor_id}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Temperature:*\n{temperature:.1f}°C"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Alert Type:*\n{alert_type}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Time:*\n{timestamp}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n{description}"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Reply to this thread or mention @sensor-bot for more details"
                    }
                ]
            }
        ]
        
        try:
            response = self.client.chat_postMessage(
                channel=self.channel,
                text=f"{severity}: {sensor_id} at {temperature:.1f}°C - {description}",
                blocks=blocks
            )
            print(f"[SlackNotifier] Alert sent: {sensor_id} ({severity})")
            return True
            
        except SlackApiError as e:
            print(f"[SlackNotifier] Failed to send: {e.response['error']}")
            return False
        except Exception as e:
            print(f"[SlackNotifier] Error: {e}")
            return False
    
    def send_fleet_alert(
        self,
        fleet_status: str,
        active_sensors: int,
        critical_count: int,
        warning_count: int,
        notes: str
    ) -> bool:
        """Send a fleet-wide status alert."""
        if not self.enabled:
            return False

        # Normalize first — whitespace / case mismatches could skip sends or route wrong footer.
        fs = (fleet_status or "").strip().upper()
        if fs not in ("FLEET_CRITICAL", "CRITICAL"):
            return False
        is_correlated_fleet = fs == "FLEET_CRITICAL"

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"FLEET STATUS: {fs}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Active Sensors:*\n{active_sensors}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Critical:*\n{critical_count}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Warning:*\n{warning_count}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Time:*\n{timestamp}"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Analysis:*\n{notes}"
                }
            },
            {
                "type": "divider"
            },
        ]
        if is_correlated_fleet:
            try:
                debounce_s = int(float(os.getenv("ANALYSIS_DEBOUNCE_SECONDS", "60")))
            except ValueError:
                debounce_s = 60
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                "Correlated fleet incident: ≥50% of tracked cooling assets in CRITICAL. "
                                f"*Automated Fleet Analysis* is queued (~{debounce_s}s debounce, then LLM; "
                                "often 2–6 min total) and posts here as a separate message when ready."
                            ),
                        }
                    ],
                }
            )
        else:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": (
                                "Localized fleet excursion: not the full correlated-fleet threshold. "
                                "No automated in-depth fleet analysis is attached to this status card."
                            ),
                        }
                    ],
                }
            )
        
        try:
            self.client.chat_postMessage(
                channel=self.channel,
                text=f"FLEET {fs}: {critical_count} critical, {warning_count} warning",
                blocks=blocks
            )
            print(f"[SlackNotifier] Fleet alert sent: {fleet_status}")
            return True
        except Exception as e:
            print(f"[SlackNotifier] Fleet alert failed: {e}")
            return False


# Singleton instance for easy import
_notifier = None

def get_notifier() -> SlackNotifier:
    """Get the singleton SlackNotifier instance."""
    global _notifier
    if _notifier is None:
        _notifier = SlackNotifier()
    return _notifier


def send_critical_alert(sensor_id: str, temperature: float, description: str, **kwargs) -> bool:
    """Convenience function to send a critical alert."""
    return get_notifier().send_critical_alert(sensor_id, temperature, description, **kwargs)


def send_fleet_alert(fleet_status: str, active_sensors: int, critical_count: int, 
                     warning_count: int, notes: str) -> bool:
    """Convenience function to send a fleet alert."""
    return get_notifier().send_fleet_alert(
        fleet_status, active_sensors, critical_count, warning_count, notes
    )


def send_message(text: str, channel: str = None) -> bool:
    """
    Send a plain text message to Slack.
    Used by fleet_alert_analyzer to push LLM analysis results.
    """
    notifier = get_notifier()
    if not notifier.enabled or not notifier.client:
        print("[SlackNotifier] Cannot send message - not enabled")
        return False
    
    try:
        target_channel = channel or notifier.channel
        notifier.client.chat_postMessage(
            channel=target_channel,
            text=text,
            mrkdwn=True
        )
        print(f"[SlackNotifier] Message sent to {target_channel}")
        return True
    except Exception as e:
        print(f"[SlackNotifier] Failed to send message: {e}")
        return False
