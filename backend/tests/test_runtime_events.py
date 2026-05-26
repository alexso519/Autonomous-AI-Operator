"""Tests for unified runtime events."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Stub dependencies before loading runtime_events
streaming_mod = ModuleType("app.streaming.event_manager")
streaming_mod.event_manager = MagicMock()
class _StubStreamEvent:
    def __init__(self, type="", timestamp="", agent="system", message="", data=None):
        self.type = type
        self.timestamp = timestamp
        self.agent = agent
        self.message = message
        self.data = data or {}

streaming_mod.StreamEvent = _StubStreamEvent
sys.modules["app.streaming.event_manager"] = streaming_mod

manager_mod = ModuleType("app.execution.manager")
manager_mod.execution_manager = MagicMock()
manager_mod.execution_manager.is_cancellation_requested = MagicMock(return_value=False)
sys.modules["app.execution.manager"] = manager_mod

db_mod = ModuleType("app.database.database")
db_mod.get_db = AsyncMock()
sys.modules["app.database.database"] = db_mod

_re_path = Path(__file__).parent.parent / "app" / "execution" / "runtime_events.py"
_re_spec = importlib.util.spec_from_file_location("runtime_events_test", _re_path)
_re_mod = importlib.util.module_from_spec(_re_spec)
sys.modules[_re_spec.name] = _re_mod
assert _re_spec.loader is not None
_re_spec.loader.exec_module(_re_mod)

RuntimeEventBus = _re_mod.RuntimeEventBus
RuntimeEventCategory = _re_mod.RuntimeEventCategory
RuntimeEventPayload = _re_mod.RuntimeEventPayload


async def test_event_bus_deduplicates_identical_events() -> None:
    bus = RuntimeEventBus("exec-events-001")
    streaming_mod.event_manager.emit = AsyncMock()
    manager_mod.execution_manager.is_cancellation_requested.return_value = False

    first = await bus.emit("agent_started", "Agent", "Starting...")
    second = await bus.emit("agent_started", "Agent", "Starting...")

    assert first is True
    assert second is False
    assert bus.stats["dedupeCount"] == 1


def test_event_payload_sse_compatible() -> None:
    payload = RuntimeEventPayload(
        event_type="quality_scored",
        agent="Test Agent",
        message="Score: 80%",
        category=RuntimeEventCategory.QUALITY,
        correlation_id="corr-123",
        execution_id="exec-001",
        data={"qualityScore": 0.8},
    )
    event = payload.to_stream_event()

    assert event.type == "quality_scored"
    assert event.data["category"] == "quality"
    assert event.data["correlationId"] == "corr-123"
    assert event.data["qualityScore"] == 0.8


async def test_event_replay_history() -> None:
    bus = RuntimeEventBus("exec-events-replay")
    streaming_mod.event_manager.emit = AsyncMock()
    manager_mod.execution_manager.is_cancellation_requested.return_value = False

    with patch.object(bus, "_persist_event", new_callable=AsyncMock):
        await bus.emit("workflow_started", "system", "Started", skip_dedupe=True)
        await bus.emit("agent_started", "Agent", "Go", skip_dedupe=True)

    history = bus.get_replay_history()
    assert len(history) == 2
    assert history[0]["eventType"] == "workflow_started"
    assert history[1]["eventType"] == "agent_started"


def test_event_category_mapping() -> None:
    assert RuntimeEventBus.category_for("reflection_action") == RuntimeEventCategory.RETRY
    assert RuntimeEventBus.category_for("workflow_completed") == RuntimeEventCategory.LIFECYCLE
    assert RuntimeEventBus.category_for("unknown_type") == RuntimeEventCategory.EXECUTION


if __name__ == "__main__":
    asyncio.run(test_event_bus_deduplicates_identical_events())
    test_event_payload_sse_compatible()
    asyncio.run(test_event_replay_history())
    test_event_category_mapping()
    print("All runtime events tests passed.")
