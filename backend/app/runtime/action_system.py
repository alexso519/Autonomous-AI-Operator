"""
Unified action system — every runtime operation is a typed RuntimeAction.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    AGENT_EXECUTION = "agent_execution"
    TOOL_CALL = "tool_call"
    WEB_RESEARCH = "web_research"
    BROWSER_ACTION = "browser_action"
    COMPUTER_USE_ACTION = "computer_use_action"
    COMPUTER_WORKFLOW_ACTION = "computer_workflow_action"
    CODE_PATCH = "code_patch"
    TEST_RUN = "test_run"
    MEMORY_RETRIEVAL = "memory_retrieval"
    SYNTHESIS = "synthesis"
    REFLECTION = "reflection"
    RETRY = "retry"
    VALIDATION = "validation"


class ActionPriority(int, Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


class ActionStatus(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


@dataclass
class ActionContext:
    """Execution context attached to every action."""

    execution_id: str
    workflow_id: str = ""
    node_id: str = ""
    agent_name: str = "system"
    correlation_id: str = ""
    parent_action_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeAction:
    """Typed runtime action with deterministic identity and provenance."""

    action_type: ActionType
    context: ActionContext
    payload: dict[str, Any] = field(default_factory=dict)
    priority: ActionPriority = ActionPriority.NORMAL
    dependencies: list[str] = field(default_factory=list)
    id: str = field(default="")
    status: ActionStatus = ActionStatus.PENDING
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    started_at: str = ""
    completed_at: str = ""
    provenance: list[str] = field(default_factory=list)
    replay_metadata: dict[str, Any] = field(default_factory=dict)
    _cancel_requested: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self._deterministic_id()
        if not self.context.correlation_id:
            self.context.correlation_id = uuid.uuid4().hex[:16]

    def _deterministic_id(self) -> str:
        raw = (
            f"{self.action_type.value}|{self.context.execution_id}|"
            f"{self.context.node_id}|{self.created_at}|{uuid.uuid4().hex[:8]}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def request_cancellation(self) -> None:
        self._cancel_requested = True

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_requested or self.status == ActionStatus.CANCELLED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "actionType": self.action_type.value,
            "status": self.status.value,
            "priority": self.priority.value,
            "executionId": self.context.execution_id,
            "nodeId": self.context.node_id,
            "agentName": self.context.agent_name,
            "correlationId": self.context.correlation_id,
            "parentActionId": self.context.parent_action_id,
            "dependencies": self.dependencies,
            "retryCount": self.retry_count,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "provenance": self.provenance,
            "payload": self.payload,
        }


@dataclass
class ActionResult:
    """Result of a completed action."""

    action_id: str
    success: bool
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    retry_lineage: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "success": self.success,
            "error": self.error,
            "durationMs": self.duration_ms,
            "retryLineage": self.retry_lineage,
            "metadata": self.metadata,
        }
