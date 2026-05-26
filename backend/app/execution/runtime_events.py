"""
Unified runtime event schema and EventBus.

Single canonical event pipeline with deduplication, correlation IDs,
and SSE-compatible emission.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

from app.database.database import get_db
from app.execution.manager import execution_manager
from app.streaming.event_manager import event_manager, StreamEvent

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class RuntimeEventCategory(str, Enum):
    EXECUTION = "execution"
    COGNITION = "cognition"
    RETRY = "retry"
    TOOL = "tool"
    BROWSER = "browser"
    CODING = "coding"
    BENCHMARK = "benchmark"
    MEMORY = "memory"
    QUALITY = "quality"
    LIFECYCLE = "lifecycle"
    TELEMETRY = "telemetry"


# Map legacy SSE event types to categories for normalized metadata
_EVENT_CATEGORY_MAP: dict[str, RuntimeEventCategory] = {
    "workflow_started": RuntimeEventCategory.LIFECYCLE,
    "workflow_completed": RuntimeEventCategory.LIFECYCLE,
    "workflow_failed": RuntimeEventCategory.LIFECYCLE,
    "workflow_cancelled": RuntimeEventCategory.LIFECYCLE,
    "agent_started": RuntimeEventCategory.EXECUTION,
    "agent_completed": RuntimeEventCategory.EXECUTION,
    "thinking": RuntimeEventCategory.EXECUTION,
    "output": RuntimeEventCategory.EXECUTION,
    "log": RuntimeEventCategory.EXECUTION,
    "quality_scored": RuntimeEventCategory.QUALITY,
    "reflection_action": RuntimeEventCategory.RETRY,
    "reflection_limit_reached": RuntimeEventCategory.RETRY,
    "confidence_updated": RuntimeEventCategory.COGNITION,
    "execution_heartbeat": RuntimeEventCategory.EXECUTION,
    "execution_stalled": RuntimeEventCategory.EXECUTION,
    "execution_recovery": RuntimeEventCategory.RETRY,
    "runtime_health_warning": RuntimeEventCategory.TELEMETRY,
    "runtime_complexity_warning": RuntimeEventCategory.TELEMETRY,
    "runtime_contract_violation": RuntimeEventCategory.TELEMETRY,
    "retry_decision": RuntimeEventCategory.RETRY,
    "lifecycle_transition": RuntimeEventCategory.LIFECYCLE,
    "graph_reasoning_started": RuntimeEventCategory.MEMORY,
    "reasoning_path_discovered": RuntimeEventCategory.MEMORY,
    "causal_link_inferred": RuntimeEventCategory.MEMORY,
    "contradiction_resolved": RuntimeEventCategory.MEMORY,
    "belief_updated": RuntimeEventCategory.MEMORY,
    "synthesized_knowledge_created": RuntimeEventCategory.MEMORY,
    "concept_discovered": RuntimeEventCategory.MEMORY,
    "strategic_insight_generated": RuntimeEventCategory.MEMORY,
    "predictive_world_state_generated": RuntimeEventCategory.MEMORY,
    "screenshot_captured": RuntimeEventCategory.BROWSER,
    "ui_element_detected": RuntimeEventCategory.BROWSER,
    "visual_grounding_completed": RuntimeEventCategory.BROWSER,
    "mouse_action_planned": RuntimeEventCategory.BROWSER,
    "keyboard_action_executed": RuntimeEventCategory.BROWSER,
    "ui_navigation_started": RuntimeEventCategory.BROWSER,
    "ui_navigation_failed": RuntimeEventCategory.BROWSER,
    "multimodal_reasoning_started": RuntimeEventCategory.BROWSER,
    "interaction_recovered": RuntimeEventCategory.BROWSER,
    "desktop_state_updated": RuntimeEventCategory.BROWSER,
    "desktop_session_started": RuntimeEventCategory.BROWSER,
    "desktop_session_restored": RuntimeEventCategory.BROWSER,
    "workspace_snapshot_saved": RuntimeEventCategory.BROWSER,
    "application_state_updated": RuntimeEventCategory.BROWSER,
    "semantic_ui_parsed": RuntimeEventCategory.BROWSER,
    "layout_reasoned": RuntimeEventCategory.BROWSER,
    "interaction_predicted": RuntimeEventCategory.BROWSER,
    "ui_similarity_detected": RuntimeEventCategory.BROWSER,
    "workflow_chain_started": RuntimeEventCategory.BROWSER,
    "workflow_step_completed": RuntimeEventCategory.BROWSER,
    "workflow_branch_created": RuntimeEventCategory.BROWSER,
    "ui_recovery_attempted": RuntimeEventCategory.BROWSER,
    "workflow_resumed": RuntimeEventCategory.BROWSER,
    "screen_change_detected": RuntimeEventCategory.BROWSER,
    "attention_shifted": RuntimeEventCategory.BROWSER,
    "multimodal_context_updated": RuntimeEventCategory.BROWSER,
    "visual_stall_detected": RuntimeEventCategory.BROWSER,
    "application_switched": RuntimeEventCategory.BROWSER,
    "cross_app_workflow_started": RuntimeEventCategory.BROWSER,
    "file_workflow_completed": RuntimeEventCategory.BROWSER,
    "system_interaction_detected": RuntimeEventCategory.BROWSER,
    # Self-improvement (bounded, inspectable)
    "heuristic_evolved": RuntimeEventCategory.COGNITION,
    "strategy_mutated": RuntimeEventCategory.COGNITION,
    "optimization_applied": RuntimeEventCategory.TELEMETRY,
    "failure_pattern_detected": RuntimeEventCategory.COGNITION,
    "runtime_learning_updated": RuntimeEventCategory.TELEMETRY,
    "prompt_optimized": RuntimeEventCategory.COGNITION,
    "planning_profile_updated": RuntimeEventCategory.COGNITION,
    "retry_policy_evolved": RuntimeEventCategory.RETRY,
    "tool_selection_optimized": RuntimeEventCategory.TOOL,
    "benchmark_regression_detected": RuntimeEventCategory.BENCHMARK,
    "performance_profile_evolved": RuntimeEventCategory.BENCHMARK,
    "capability_trend_updated": RuntimeEventCategory.BENCHMARK,
    "benchmark_optimization_completed": RuntimeEventCategory.BENCHMARK,
    "runtime_adapted": RuntimeEventCategory.TELEMETRY,
    "resource_profile_updated": RuntimeEventCategory.TELEMETRY,
    "adaptive_constraint_triggered": RuntimeEventCategory.TELEMETRY,
    "execution_strategy_reweighted": RuntimeEventCategory.EXECUTION,
    "improvement_cycle_started": RuntimeEventCategory.COGNITION,
    "improvement_cycle_completed": RuntimeEventCategory.COGNITION,
    "optimization_rollback_triggered": RuntimeEventCategory.RETRY,
    "evolution_policy_updated": RuntimeEventCategory.COGNITION,
}

# Event types that should not also emit a duplicate "log" event
_SUPPRESS_DUPLICATE_LOG_TYPES = frozenset({
    "agent_started",
    "agent_completed",
    "output",
    "workflow_started",
    "workflow_completed",
    "workflow_failed",
    "workflow_cancelled",
    "approval_requested",
})


@dataclass
class RuntimeEventPayload:
    """Canonical runtime event payload."""

    event_type: str
    agent: str
    message: str
    category: RuntimeEventCategory = RuntimeEventCategory.EXECUTION
    correlation_id: str = ""
    execution_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    data: dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""

    def to_stream_event(self) -> StreamEvent:
        enriched = {
            **self.data,
            "category": self.category.value,
            "correlationId": self.correlation_id,
            "executionId": self.execution_id,
        }
        return StreamEvent(
            type=self.event_type,
            timestamp=self.timestamp,
            agent=self.agent,
            message=self.message,
            data=enriched,
        )

    def to_replay_dict(self) -> dict[str, Any]:
        return {
            "eventType": self.event_type,
            "category": self.category.value,
            "correlationId": self.correlation_id,
            "executionId": self.execution_id,
            "timestamp": self.timestamp,
            "agent": self.agent,
            "message": self.message,
            "data": self.data,
        }


class RuntimeEventBus:
    """
    Centralized event emission with deduplication and correlation.

    Preserves SSE/frontend compatibility by emitting the same event types
    the frontend already consumes.
    """

    DEDUPE_WINDOW_SECONDS = 2.0

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.correlation_id = uuid.uuid4().hex[:16]
        self._recent_hashes: dict[str, float] = {}
        self._event_history: list[RuntimeEventPayload] = []
        self._emit_count = 0
        self._dedupe_count = 0

    @classmethod
    def category_for(cls, event_type: str) -> RuntimeEventCategory:
        return _EVENT_CATEGORY_MAP.get(event_type, RuntimeEventCategory.EXECUTION)

    def _dedupe_hash(self, event_type: str, agent: str, message: str) -> str:
        raw = f"{event_type}|{agent}|{message[:200]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _is_duplicate(self, dedupe_key: str) -> bool:
        import time

        now = time.monotonic()
        # Purge stale entries
        stale = [k for k, t in self._recent_hashes.items() if now - t > self.DEDUPE_WINDOW_SECONDS]
        for k in stale:
            del self._recent_hashes[k]

        if dedupe_key in self._recent_hashes:
            return True
        self._recent_hashes[dedupe_key] = now
        return False

    async def emit(
        self,
        event_type: str,
        agent: str,
        message: str,
        *,
        skip_dedupe: bool = False,
        skip_persist: bool = False,
        **data: Any,
    ) -> bool:
        """
        Emit a runtime event. Returns False if deduplicated or cancelled.

        SSE-compatible: produces the same StreamEvent shape as legacy _emit().
        """
        if execution_manager.is_cancellation_requested(self.execution_id):
            return False

        dedupe_key = self._dedupe_hash(event_type, agent, message)
        if not skip_dedupe and self._is_duplicate(dedupe_key):
            self._dedupe_count += 1
            return False

        payload = RuntimeEventPayload(
            event_type=event_type,
            agent=agent,
            message=message,
            category=self.category_for(event_type),
            correlation_id=self.correlation_id,
            execution_id=self.execution_id,
            data=data,
            dedupe_key=dedupe_key,
        )

        event = payload.to_stream_event()
        await event_manager.emit(self.execution_id, event)

        if not skip_persist:
            await self._persist_event(event_type, event.timestamp, agent, message, data)

        self._event_history.append(payload)
        self._emit_count += 1
        return True

    async def emit_log_if_needed(
        self,
        event_type: str,
        agent: str,
        message: str,
        log_id: str,
        level: str = "info",
    ) -> None:
        """Emit a log event only when it adds information beyond the primary event."""
        if event_type in _SUPPRESS_DUPLICATE_LOG_TYPES:
            return
        await self.emit(
            "log",
            agent,
            message,
            logId=log_id,
            level=level,
        )

    async def end_stream(self) -> None:
        """Send sentinel to end the SSE stream."""
        if not execution_manager.is_cancellation_requested(self.execution_id):
            await event_manager.emit(self.execution_id, None)

    async def _persist_event(
        self,
        event_type: str,
        timestamp: str,
        agent: str,
        message: str,
        data: dict[str, Any],
    ) -> None:
        try:
            db = await get_db()
            await db.execute(
                """INSERT INTO execution_event_logs
                   (execution_id, event_type, timestamp, agent, message, data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    self.execution_id,
                    event_type,
                    timestamp,
                    agent,
                    message[:1000],
                    json.dumps(data),
                ),
            )
            await db.commit()
        except Exception:
            logger.exception(
                "Failed to persist event for execution %s", self.execution_id
            )

    def get_replay_history(self) -> list[dict[str, Any]]:
        """Return event history for deterministic replay."""
        return [p.to_replay_dict() for p in self._event_history]

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "emitCount": self._emit_count,
            "dedupeCount": self._dedupe_count,
            "historySize": len(self._event_history),
            "correlationId": self.correlation_id,
        }


def make_legacy_emit_fn(bus: RuntimeEventBus) -> EmitFn:
    """Wrap EventBus as a legacy-compatible emit_fn(execution_id, type, agent, msg, **data)."""

    async def _emit(
        execution_id: str,
        event_type: str,
        agent: str,
        message: str,
        **data: Any,
    ) -> None:
        await bus.emit(event_type, agent, message, **data)

    return _emit
