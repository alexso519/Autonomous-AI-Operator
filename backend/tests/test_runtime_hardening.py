"""Tests for runtime hardening."""

from app.execution.runtime_hardening import ExecutionPriority, RuntimeHardening


def test_queue_enqueue_dequeue():
    RuntimeHardening._queue.clear()
    assert RuntimeHardening.enqueue("exec-1", ExecutionPriority.NORMAL)
    assert RuntimeHardening.enqueue("exec-2", ExecutionPriority.HIGH)
    assert RuntimeHardening.queue_depth() == 2
    first = RuntimeHardening.dequeue()
    assert first is not None
    assert first.priority == ExecutionPriority.HIGH


def test_backpressure():
    RuntimeHardening._queue.clear()
    for i in range(RuntimeHardening.BACKPRESSURE_THRESHOLD):
        RuntimeHardening.enqueue(f"q-{i}")
    ok, reason = RuntimeHardening.check_backpressure(active_count=1)
    assert not ok
    assert reason == "queue_backpressure_limit"
