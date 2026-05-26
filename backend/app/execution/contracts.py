"""
Typed execution contracts for the consolidated runtime.

Reduces implicit dict mutation across runtime modules by providing
stable, inspectable data shapes for cross-module communication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.execution.execution_quality import ExecutionQualityScore
from app.execution.retry_policy import RetryDecision, RetryType
from app.execution.runtime_state import RuntimeState


class RetryIntent(str, Enum):
    """Source category for a retry request."""

    REFLECTION = "reflection"
    TOOL = "tool"
    RECOVERY = "recovery"
    DEBUG = "debug"
    STALL = "stall"
    QUALITY = "quality"


@dataclass(frozen=True)
class ExecutionContextContract:
    """Snapshot of execution context passed between runtime layers."""

    execution_id: str
    workflow_id: str
    workflow_name: str
    workflow_description: str
    node_count: int
    global_retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryRequest:
    """Unified retry request submitted to RetryCoordinator."""

    execution_id: str
    node_id: str
    agent_name: str
    intent: RetryIntent
    reason: str
    quality: ExecutionQualityScore | None = None
    prior_output: str = ""
    legacy_issues: list[str] = field(default_factory=list)
    output_hash: str | None = None


@dataclass
class ReflectionOutcome:
    """Result of centralized reflection evaluation."""

    node_id: str
    agent_name: str
    should_replan: bool
    action: str
    recovery_type: str
    retry_type: str
    reason: str
    reasoning_trail: list[str]
    quality_score: float
    issues: list[str]
    retry_count: int
    retry_decision: RetryDecision | None = None
    suppressed: bool = False
    suppression_reason: str = ""


@dataclass
class ToolInvocationOutcomeContract:
    """Normalized tool invocation result for runtime contracts."""

    tool_name: str
    node_id: str
    status: str
    output: str = ""
    error: str | None = None
    requires_retry: bool = False
    retry_intent: RetryIntent = RetryIntent.TOOL


@dataclass
class RuntimeTransition:
    """Inspectable runtime state transition record."""

    execution_id: str
    from_state: RuntimeState
    to_state: RuntimeState
    reason: str
    phase: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    transition_id: str = ""


@dataclass
class SynthesisInputContract:
    """Inputs for final response synthesis."""

    workflow_name: str
    objective: str
    steps: list[dict[str, Any]]
    agent_outputs: dict[str, str]
    retry_count: int = 0
    tool_count: int = 0


@dataclass
class QualityResultContract:
    """Normalized quality scoring result."""

    node_id: str
    agent_name: str
    overall_score: float
    issues: list[str]
    should_retry: bool
    retry_hints: list[str]
    score: ExecutionQualityScore | None = None

    @classmethod
    def from_score(cls, score: ExecutionQualityScore) -> QualityResultContract:
        return cls(
            node_id=score.node_id,
            agent_name=score.agent_name,
            overall_score=score.overall_score,
            issues=list(score.issues),
            should_retry=score.should_retry,
            retry_hints=list(score.retry_hints),
            score=score,
        )


@dataclass
class RetryCoordinatorResult:
    """Outcome of the unified retry decision pipeline."""

    allowed: bool
    decision: RetryDecision | None
    retry_type: RetryType | None
    block_reason: str = ""
    deduplicated: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)
