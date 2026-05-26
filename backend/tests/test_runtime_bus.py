"""Tests for unified runtime bus."""

import asyncio

from app.runtime.runtime_bus import RuntimeBus, RuntimeChannel, RuntimeMessage


def test_publish_and_replay():
    async def _run():
        bus = RuntimeBus("bus-test-1")
        ok = await bus.publish(
            RuntimeChannel.EXECUTION,
            "test_event",
            "hello",
            agent="system",
            skip_dedupe=True,
        )
        assert ok is True
        assert len(bus.replay()) == 1

    asyncio.run(_run())


def test_deduplication():
    async def _run():
        bus = RuntimeBus("bus-test-2")
        await bus.publish(RuntimeChannel.EXECUTION, "dup", "same message")
        second = await bus.publish(RuntimeChannel.EXECUTION, "dup", "same message")
        assert second is False

    asyncio.run(_run())


def test_subscribe_handler():
    async def _run():
        bus = RuntimeBus("bus-test-3")
        received: list[RuntimeMessage] = []

        async def handler(msg: RuntimeMessage) -> None:
            received.append(msg)

        bus.subscribe(RuntimeChannel.TOOLS, handler)
        await bus.publish(
            RuntimeChannel.TOOLS,
            "tool_started",
            "tool run",
            skip_dedupe=True,
        )
        assert len(received) == 1
        assert received[0].event_type == "tool_started"

    asyncio.run(_run())


def test_backpressure_emits_event():
    async def _run():
        bus = RuntimeBus("bus-test-4")
        bus.queue_size = 2
        bus._replay_buffer = bus._replay_buffer.__class__(maxlen=2)
        await bus.publish(RuntimeChannel.EXECUTION, "e1", "m1", skip_dedupe=True)
        await bus.publish(RuntimeChannel.EXECUTION, "e2", "m2", skip_dedupe=True)
        await bus.publish(RuntimeChannel.EXECUTION, "e3", "m3", skip_dedupe=True)
        assert bus.stats["droppedCount"] >= 1 or any(
            m.event_type == "runtime_backpressure" for m in bus.replay()
        )

    asyncio.run(_run())


def test_message_dedupe_key():
    msg = RuntimeMessage(
        channel=RuntimeChannel.MEMORY,
        event_type="memory_sync",
        execution_id="e1",
        message="sync",
    )
    assert len(msg.dedupe_key()) == 16
