"""
Legacy event adapter — routes RuntimeBus messages to SSE via existing emit_fn.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from app.runtime.runtime_bus import RuntimeBus, RuntimeChannel, RuntimeMessage

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

# Kernel-native events that should also reach SSE consumers
_SSE_FORWARD_TYPES = frozenset({
    "kernel_initialized",
    "action_scheduled",
    "action_completed",
    "scheduler_pressure",
    "runtime_backpressure",
    "capability_failure",
    "runtime_recovered",
    "capability_registered",
    "capability_selected",
    "capability_unavailable",
    "parallel_group_started",
})


class LegacyEventAdapter:
    """
    Bridges RuntimeBus publish calls to legacy SSE emit_fn and RuntimeEventBus.
    """

    def __init__(
        self,
        bus: RuntimeBus,
        emit_fn: EmitFn,
        execution_id: str,
    ) -> None:
        self.bus = bus
        self.emit_fn = emit_fn
        self.execution_id = execution_id
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self.bus.set_sse_bridge(self._bridge_to_sse)
        self._installed = True

    async def _bridge_to_sse(self, msg: RuntimeMessage) -> None:
        if msg.event_type not in _SSE_FORWARD_TYPES:
            return
        try:
            await self.emit_fn(
                self.execution_id,
                msg.event_type,
                msg.agent,
                msg.message,
                channel=msg.channel.value,
                **msg.data,
            )
        except Exception:
            logger.exception("Legacy SSE bridge failed for %s", msg.event_type)

    @staticmethod
    def wrap_emit_with_bus(
        bus: RuntimeBus,
        emit_fn: EmitFn,
        execution_id: str,
    ) -> EmitFn:
        """Wrap emit_fn to also publish on RuntimeBus."""

        async def _wrapped(
            exec_id: str,
            event_type: str,
            agent: str,
            message: str,
            **data: Any,
        ) -> None:
            channel = _event_type_to_channel(event_type)
            await bus.publish(
                channel,
                event_type,
                message,
                agent=agent,
                skip_dedupe=True,
                **data,
            )
            await emit_fn(exec_id, event_type, agent, message, **data)

        return _wrapped


def _event_type_to_channel(event_type: str) -> RuntimeChannel:
    mapping = {
        "confidence_": RuntimeChannel.COGNITION,
        "reflection_": RuntimeChannel.REFLECTION,
        "retry_": RuntimeChannel.RETRY,
        "tool_": RuntimeChannel.TOOLS,
        "browser_": RuntimeChannel.BROWSER,
        "screenshot_": RuntimeChannel.BROWSER,
        "ui_": RuntimeChannel.BROWSER,
        "visual_": RuntimeChannel.BROWSER,
        "mouse_": RuntimeChannel.BROWSER,
        "keyboard_": RuntimeChannel.BROWSER,
        "multimodal_": RuntimeChannel.BROWSER,
        "interaction_": RuntimeChannel.BROWSER,
        "desktop_": RuntimeChannel.BROWSER,
        "coding_": RuntimeChannel.CODING,
        "patch_": RuntimeChannel.CODING,
        "memory_": RuntimeChannel.MEMORY,
        "plan_": RuntimeChannel.PLANNING,
        "strategy_": RuntimeChannel.PLANNING,
        "kernel_": RuntimeChannel.TELEMETRY,
        "action_": RuntimeChannel.EXECUTION,
        "scheduler_": RuntimeChannel.TELEMETRY,
        "runtime_": RuntimeChannel.TELEMETRY,
        "capability_": RuntimeChannel.TELEMETRY,
    }
    for prefix, channel in mapping.items():
        if event_type.startswith(prefix):
            return channel
    return RuntimeChannel.EXECUTION
