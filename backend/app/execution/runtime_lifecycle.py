"""
Unified runtime lifecycle manager.

Canonical execution states, normalized transitions, phase tracking,
and standardized failure propagation. Replaces fragmented transition
logic across engine.py, runtime_state.py, and execution_stability.py.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from app.execution.contracts import RuntimeTransition
from app.execution.runtime_state import (
    RuntimeState,
    get_latest_state,
    get_state_history,
    transition_execution_state,
    init_execution_state,
)

logger = logging.getLogger(__name__)

LifecycleHook = Callable[[RuntimeTransition], Awaitable[None] | None]


class ExecutionPhase(str, Enum):
    """Runtime phase within a lifecycle state."""

    INITIALIZING = "initializing"
    PRE_EXECUTION = "pre_execution"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    REFLECTING = "reflecting"
    REPLANNING = "replanning"
    SYNTHESIZING = "synthesizing"
    POST_EXECUTION = "post_execution"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Valid state transitions (canonical — superset of runtime_state rules)
_VALID_TRANSITIONS: dict[RuntimeState, frozenset[RuntimeState]] = {
    RuntimeState.CREATED: frozenset({
        RuntimeState.PENDING, RuntimeState.RUNNING,
        RuntimeState.CANCELLED, RuntimeState.FAILED,
    }),
    RuntimeState.PENDING: frozenset({
        RuntimeState.RUNNING, RuntimeState.CANCELLED, RuntimeState.FAILED,
    }),
    RuntimeState.RUNNING: frozenset({
        RuntimeState.WAITING_APPROVAL, RuntimeState.COMPLETED,
        RuntimeState.CANCELLED, RuntimeState.FAILED,
    }),
    RuntimeState.WAITING_APPROVAL: frozenset({
        RuntimeState.RUNNING, RuntimeState.REJECTED, RuntimeState.CANCELLED,
    }),
    RuntimeState.COMPLETED: frozenset(),
    RuntimeState.CANCELLED: frozenset(),
    RuntimeState.FAILED: frozenset(),
    RuntimeState.REJECTED: frozenset(),
    RuntimeState.ORPHANED: frozenset(),
}

_TERMINAL_STATES = frozenset({
    RuntimeState.COMPLETED,
    RuntimeState.CANCELLED,
    RuntimeState.FAILED,
    RuntimeState.REJECTED,
    RuntimeState.ORPHANED,
})


@dataclass
class LifecycleContext:
    """Per-execution lifecycle tracking."""

    execution_id: str
    current_state: RuntimeState = RuntimeState.CREATED
    current_phase: ExecutionPhase = ExecutionPhase.INITIALIZING
    transition_history: list[RuntimeTransition] = field(default_factory=list)
    _transition_lock: bool = False


class LifecycleManager:
    """
    Manages deterministic runtime state transitions.

    Features:
      - No recursive transition calls (lock guard)
      - Deterministic transition validation
      - Inspectable in-memory transition history
      - State replay support
      - Lifecycle hooks
    """

    _contexts: dict[str, LifecycleContext] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        if execution_id not in self._contexts:
            self._contexts[execution_id] = LifecycleContext(execution_id=execution_id)
        self._ctx = self._contexts[execution_id]
        self._before_hooks: list[LifecycleHook] = []
        self._after_hooks: list[LifecycleHook] = []

    @classmethod
    def get_context(cls, execution_id: str) -> LifecycleContext | None:
        return cls._contexts.get(execution_id)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._contexts.pop(execution_id, None)

    def register_before_hook(self, hook: LifecycleHook) -> None:
        self._before_hooks.append(hook)

    def register_after_hook(self, hook: LifecycleHook) -> None:
        self._after_hooks.append(hook)

    def set_phase(self, phase: ExecutionPhase) -> None:
        self._ctx.current_phase = phase

    @property
    def current_phase(self) -> ExecutionPhase:
        return self._ctx.current_phase

    @property
    def current_state(self) -> RuntimeState:
        return self._ctx.current_state

    def is_valid_transition(
        self,
        from_state: RuntimeState,
        to_state: RuntimeState,
    ) -> bool:
        if from_state == to_state:
            return True
        if from_state is None or from_state == RuntimeState.CREATED:
            return True
        allowed = _VALID_TRANSITIONS.get(from_state, frozenset())
        return to_state in allowed

    async def transition(
        self,
        to_state: RuntimeState,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        from_state: RuntimeState | str | None = None,
        phase: ExecutionPhase | None = None,
    ) -> RuntimeState:
        """
        Perform a validated state transition.

        Raises no exceptions — invalid transitions are logged and skipped
        to avoid breaking execution (matches legacy behavior).
        """
        if self._ctx._transition_lock:
            logger.warning(
                "Recursive transition blocked for %s: %s → %s",
                self.execution_id,
                self._ctx.current_state.value,
                to_state.value if isinstance(to_state, RuntimeState) else to_state,
            )
            return self._ctx.current_state

        normalized_to = to_state if isinstance(to_state, RuntimeState) else RuntimeState(to_state)

        if from_state is not None:
            normalized_from = (
                from_state if isinstance(from_state, RuntimeState) else RuntimeState(from_state)
            )
        else:
            try:
                db_state = await get_latest_state(self.execution_id)
                normalized_from = db_state or self._ctx.current_state
            except Exception:
                normalized_from = self._ctx.current_state

        if normalized_from is None:
            normalized_from = RuntimeState.CREATED

        if normalized_from == normalized_to:
            if phase:
                self._ctx.current_phase = phase
            return normalized_to

        if not self.is_valid_transition(normalized_from, normalized_to):
            logger.warning(
                "Invalid lifecycle transition %s → %s for %s (reason: %s)",
                normalized_from.value,
                normalized_to.value,
                self.execution_id,
                reason,
            )

        record = RuntimeTransition(
            execution_id=self.execution_id,
            from_state=normalized_from,
            to_state=normalized_to,
            reason=reason or "",
            phase=(phase or self._ctx.current_phase).value,
            metadata=metadata or {},
            transition_id=uuid.uuid4().hex[:12],
        )

        self._ctx._transition_lock = True
        try:
            for hook in self._before_hooks:
                result = hook(record)
                if result is not None:
                    await result

            try:
                await transition_execution_state(
                    execution_id=self.execution_id,
                    to_state=normalized_to,
                    reason=reason,
                    metadata=metadata,
                    from_state=normalized_from,
                )
            except Exception as exc:
                logger.warning(
                    "Could not persist lifecycle transition for %s: %s",
                    self.execution_id,
                    exc,
                )

            self._ctx.current_state = normalized_to
            self._ctx.transition_history.append(record)
            if phase:
                self._ctx.current_phase = phase
            elif normalized_to in _TERMINAL_STATES:
                phase_map = {
                    RuntimeState.COMPLETED: ExecutionPhase.COMPLETED,
                    RuntimeState.CANCELLED: ExecutionPhase.CANCELLED,
                    RuntimeState.FAILED: ExecutionPhase.FAILED,
                }
                self._ctx.current_phase = phase_map.get(normalized_to, self._ctx.current_phase)

            for hook in self._after_hooks:
                result = hook(record)
                if result is not None:
                    await result

        finally:
            self._ctx._transition_lock = False

        return normalized_to

    async def initialize(
        self,
        workflow_id: str,
        initial_state: RuntimeState = RuntimeState.PENDING,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeState:
        """Initialize execution lifecycle (wraps init_execution_state)."""
        if metadata is None:
            metadata = {}
        metadata["workflowId"] = workflow_id
        self.set_phase(ExecutionPhase.INITIALIZING)

        try:
            state = await init_execution_state(
                execution_id=self.execution_id,
                workflow_id=workflow_id,
                initial_state=initial_state,
                reason=reason,
                metadata=metadata,
            )
        except Exception as exc:
            logger.warning("Lifecycle init failed for %s: %s", self.execution_id, exc)
            state = initial_state

        self._ctx.current_state = state
        return state

    async def propagate_failure(
        self,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeState:
        """Standardized failure propagation."""
        return await self.transition(
            RuntimeState.FAILED,
            reason=reason,
            metadata=metadata,
            phase=ExecutionPhase.FAILED,
        )

    def get_transition_history(self) -> list[RuntimeTransition]:
        return list(self._ctx.transition_history)

    async def get_persisted_history(self) -> list[dict[str, Any]]:
        """Load transition history from database for replay."""
        try:
            return await get_state_history(self.execution_id)
        except Exception:
            return [
                {
                    "id": t.transition_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "fromState": t.from_state.value,
                    "toState": t.to_state.value,
                    "reason": t.reason,
                    "metadata": t.metadata,
                }
                for t in self._ctx.transition_history
            ]

    def replay_transitions(self) -> list[dict[str, Any]]:
        """Return in-memory transitions for deterministic replay."""
        return [
            {
                "transitionId": t.transition_id,
                "fromState": t.from_state.value,
                "toState": t.to_state.value,
                "reason": t.reason,
                "phase": t.phase,
                "metadata": t.metadata,
            }
            for t in self._ctx.transition_history
        ]
