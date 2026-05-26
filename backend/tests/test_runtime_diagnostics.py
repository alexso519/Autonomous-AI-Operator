"""Tests for runtime diagnostics."""

from app.runtime.runtime_diagnostics import RuntimeDiagnostics


def test_action_metrics():
    diag = RuntimeDiagnostics("diag-1")
    diag.record_action_scheduled()
    diag.record_action_started()
    diag.record_action_completed(100.0)
    snap = diag.snapshot()
    assert snap["actions"]["scheduled"] == 1
    assert snap["actions"]["completed"] == 1
    assert snap["actions"]["avgLatencyMs"] == 100.0


def test_retry_storm_detection():
    diag = RuntimeDiagnostics("diag-2")
    diag.record_retry_burst(6)
    assert diag.scheduler_health.retry_storm_detected is True


def test_backpressure_tracking():
    diag = RuntimeDiagnostics("diag-3")
    diag.record_backpressure()
    assert diag.queue_metrics.backpressure_events == 1


def test_degraded_mode():
    diag = RuntimeDiagnostics("diag-4")
    diag.record_degraded_mode()
    assert diag.scheduler_health.degraded_mode is True
