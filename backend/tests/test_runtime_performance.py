"""Tests for runtime performance tracker."""

from app.execution.runtime_performance import RuntimePerformanceTracker


def test_stage_timing_and_bottleneck_detection():
    eid = "test-perf-001"
    RuntimePerformanceTracker.start_execution(eid)
    RuntimePerformanceTracker.begin_stage(eid, "agent", "Agent A")
    RuntimePerformanceTracker.end_stage(eid, "agent", "Agent A")
    RuntimePerformanceTracker.begin_stage(eid, "reflection", "Agent A")
    RuntimePerformanceTracker.end_stage(eid, "reflection", "Agent A")

    snapshot = RuntimePerformanceTracker.finalize(eid)
    assert snapshot["executionId"] == eid
    assert snapshot["totalDurationSeconds"] >= 0
    assert isinstance(snapshot["recommendations"], list)
    assert snapshot["stageCount"] >= 2


def test_recommendations_when_idle():
    eid = "test-perf-002"
    RuntimePerformanceTracker.start_execution(eid)
    active = RuntimePerformanceTracker._active[eid]
    active["idle_seconds"] = 100
    active["started_at"] = __import__("time").monotonic() - 110
    snapshot = RuntimePerformanceTracker.finalize(eid)
    types = {b["type"] for b in snapshot.get("bottlenecks", [])}
    assert "agent_idle" in types
