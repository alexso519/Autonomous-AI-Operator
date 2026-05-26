"""
Consolidated execution runtime facade.

Bundles lifecycle, retry, reflection, events, telemetry, and memory runtime
into a single runtime context used by the execution engine.
"""

from __future__ import annotations

from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.reflection_coordinator import ReflectionCoordinator
from app.execution.retry_coordinator import RetryCoordinator
from app.execution.runtime_events import RuntimeEventBus, make_legacy_emit_fn
from app.execution.runtime_lifecycle import LifecycleManager
from app.execution.runtime_telemetry import RuntimeTelemetry


class ExecutionRuntime:
    """
    Unified runtime context for a single workflow execution.

    Architecture:
      ExecutionRuntime
      ├── LifecycleManager
      ├── RetryCoordinator
      ├── ReflectionCoordinator
      ├── EventBus (RuntimeEventBus)
      ├── RuntimeTelemetry
      └── MemoryRuntime (via MemoryRuntime.for_execution)
    """

    _active: dict[str, ExecutionRuntime] = {}

    def __init__(self, execution_id: str, ctx: ContextManager | None = None) -> None:
        self.execution_id = execution_id
        self.ctx = ctx
        self.lifecycle = LifecycleManager(execution_id)
        self.events = RuntimeEventBus(execution_id)
        self.telemetry = RuntimeTelemetry(execution_id, self.events)
        self._retry: RetryCoordinator | None = None
        self._reflection: ReflectionCoordinator | None = None
        ExecutionRuntime._active[execution_id] = self

    @classmethod
    def get(cls, execution_id: str) -> ExecutionRuntime | None:
        return cls._active.get(execution_id)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._active.pop(execution_id, None)
        LifecycleManager.cleanup(execution_id)
        RetryCoordinator.cleanup(execution_id)
        RuntimeTelemetry.cleanup(execution_id)

    def bind_context(self, ctx: ContextManager) -> None:
        self.ctx = ctx
        self._retry = None
        self._reflection = None

    @property
    def retry(self) -> RetryCoordinator:
        if self._retry is None:
            assert self.ctx is not None
            self._retry = RetryCoordinator(self.execution_id, self.ctx)
        return self._retry

    @property
    def reflection(self) -> ReflectionCoordinator:
        if self._reflection is None:
            assert self.ctx is not None
            self._reflection = ReflectionCoordinator(
                self.execution_id, self.ctx, self.lifecycle
            )
        return self._reflection

    async def emit(
        self,
        event_type: str,
        agent: str,
        message: str,
        **data: Any,
    ) -> bool:
        return await self.events.emit(event_type, agent, message, **data)

    def legacy_emit_fn(self):
        """Return a legacy-compatible emit_fn for sub-orchestrators."""
        return make_legacy_emit_fn(self.events)

    async def end_stream(self) -> None:
        await self.events.end_stream()
