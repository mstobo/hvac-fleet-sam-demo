"""
Unit-style tests for fleet_alert_analyzer cleanup paths.

These cover the bugs fixed in the "fleet_alert_analyzer cleanup" commit:
  A. shutdown() must cancel a pending debounce timer.
  B. _get_mqtt_client() must tear down a stale client before creating a new one
     so the prior loop_start() thread doesn't leak across reconnects.
  C. shutdown() should reset _pending_analysis so re-init starts clean.
"""

from __future__ import annotations

import threading

import pytest


@pytest.fixture(autouse=True)
def _isolate_module(monkeypatch):
    """Each test starts with a clean module state — no leftover timer or client refs."""
    import fleet_alert_analyzer as fa
    fa._collected_criticals.clear()
    fa._collected_sensors.clear()
    fa._pending_analysis = False
    fa._pending_timer = None
    fa._mqtt_client = None
    fa._mqtt_connected = False
    yield


def test_shutdown_cancels_pending_timer():
    """A: shutdown() cancels the debounce timer so it can't fire post-disconnect."""
    import fleet_alert_analyzer as fa

    # Plant a Timer that, if it fires, would mark a flag — proves cancel worked.
    fired = threading.Event()
    timer = threading.Timer(0.5, fired.set)
    timer.daemon = True
    timer.start()
    fa._pending_timer = timer
    fa._pending_analysis = True

    fa.shutdown()

    # After shutdown:
    assert fa._pending_timer is None, "shutdown should null the timer reference"
    assert fa._pending_analysis is False, "shutdown should reset pending flag"
    # Wait long enough that the timer would have fired if not cancelled.
    assert not fired.wait(timeout=0.8), "cancelled timer must not fire"


def test_shutdown_is_idempotent():
    """C: calling shutdown() twice is safe (no exception, state stays clean)."""
    import fleet_alert_analyzer as fa
    fa.shutdown()
    fa.shutdown()  # second call should be a no-op
    assert fa._pending_timer is None
    assert fa._mqtt_client is None
    assert fa._pending_analysis is False


def test_get_mqtt_client_tears_down_stale_client(monkeypatch):
    """B: when _mqtt_connected is False but _mqtt_client exists, the stale client
    must be loop_stop'd before a replacement is created (otherwise the old
    network thread keeps running)."""
    import fleet_alert_analyzer as fa

    teardown_calls = {"loop_stop": 0, "disconnect": 0}

    class FakeStaleClient:
        def loop_stop(self):
            teardown_calls["loop_stop"] += 1

        def disconnect(self):
            teardown_calls["disconnect"] += 1

    fa._mqtt_client = FakeStaleClient()
    fa._mqtt_connected = False

    # Skip broker-config validation — this test isolates the cleanup path, not config.
    if fa.CONFIG_AVAILABLE:
        monkeypatch.setattr(fa.config, "validate_broker_config", lambda: None)

    # Force the new-client construction path to fail fast so we don't actually open MQTT.
    # The point of this test is the *cleanup* of the stale one, which happens before
    # the new client is built.
    monkeypatch.setattr(fa.mqtt, "Client", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("test-stop")))

    result = fa._get_mqtt_client()

    assert result is None, "construction failure should propagate as None"
    assert teardown_calls["loop_stop"] == 1, "stale client.loop_stop must be called exactly once"
    assert teardown_calls["disconnect"] == 1, "stale client.disconnect must be called exactly once"
    assert fa._mqtt_client is None, "module global must be cleared on failure"
