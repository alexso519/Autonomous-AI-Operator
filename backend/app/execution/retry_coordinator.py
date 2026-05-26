"""
Unified retry coordination layer.

Consolidates retry_policy, reflection retries, debug loop retries,
tool retries, and recovery retries into a single decision pipeline.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.contracts import RetryCoordinatorResult, RetryIntent, RetryRequest
from app.execution.execution_quality import ExecutionQualityScore
from app.execution.retry_policy import RetryDecision, RetryPolicy, RetryType

logger = logging.getLogger(__name__)

# Maximum consecutive retries of the same intent+node to prevent chain explosions
MAX_SAME_INTENT_RETRIES = 2
# Global cap on recovery-loop depth
MAX_RECOVERY_CHAIN_DEPTH = 3


@dataclass
class RetryTelemetryRecord:
    timestamp: str
    execution_id: str
    node_id: str
    intent: str
    retry_type: str
    allowed: bool
    reason: str
    deduplicated: bool = False
    budget_remaining: int = 0


class RetryCoordinator:
    """
    Single retry decision pipeline with budget enforcement,
    deduplication, and telemetry.
    """

    _telemetry: dict[str, list[RetryTelemetryRecord]] = {}

    def __init__(self, execution_id: str, ctx: ContextManager) -> None:
        self.execution_id = execution_id
        self.ctx = ctx

    def evaluate(self, request: RetryRequest) -> RetryCoordinatorResult:
        """
        Evaluate a retry request through the unified pipeline.

        Returns a RetryCoordinatorResult with allowed flag and optional decision.
        """
        dedupe_key = self._build_dedupe_key(request)
        if self._is_deduplicated(dedupe_key):
            result = RetryCoordinatorResult(
                allowed=False,
                decision=None,
                retry_type=None,
                block_reason="deduplicated_retry",
                deduplicated=True,
            )
            self._record_telemetry(request, result)
            return result

        if self._is_recovery_chain_exhausted(request):
            result = RetryCoordinatorResult(
                allowed=False,
                decision=None,
                retry_type=None,
                block_reason="recovery_chain_limit",
            )
            self._record_telemetry(request, result)
            return result

        can_retry, block_reason = RetryPolicy.can_retry(self.ctx, request.node_id)
        if not can_retry:
            result = RetryCoordinatorResult(
                allowed=False,
                decision=None,
                retry_type=None,
                block_reason=block_reason,
            )
            self._record_telemetry(request, result)
            return result

        quality = request.quality
        if quality is None:
            result = RetryCoordinatorResult(
                allowed=False,
                decision=None,
                retry_type=None,
                block_reason="missing_quality_score",
            )
            self._record_telemetry(request, result)
            return result

        retry_type = RetryPolicy.classify_failure(quality, request.legacy_issues)
        decision = RetryPolicy.select_strategy(
            retry_type,
            quality,
            self.ctx,
            request.prior_output,
        )

        if decision.strategy_label == "blocked":
            result = RetryCoordinatorResult(
                allowed=False,
                decision=decision,
                retry_type=retry_type,
                block_reason=decision.reason,
            )
            self._record_telemetry(request, result)
            return result

        self._mark_dedupe_key(dedupe_key)
        self._increment_intent_count(request)

        result = RetryCoordinatorResult(
            allowed=True,
            decision=decision,
            retry_type=retry_type,
            telemetry={
                "intent": request.intent.value,
                "retryType": retry_type.value,
                "budgetRemaining": decision.budget_remaining,
            },
        )
        self._record_telemetry(request, result)
        return result

    def record_attempt(
        self,
        node_id: str,
        decision: RetryDecision,
        output_hash: str | None = None,
    ) -> None:
        """Record a committed retry attempt."""
        RetryPolicy.record_retry_attempt(self.ctx, node_id, decision, output_hash)

    def build_recovery_goal(
        self,
        original_goal: str,
        agent_name: str,
        decision: RetryDecision,
        quality: ExecutionQualityScore,
    ) -> str:
        return RetryPolicy.build_recovery_goal(
            original_goal, agent_name, decision, quality
        )

    def _build_dedupe_key(self, request: RetryRequest) -> str:
        output_part = request.output_hash or hashlib.sha256(
            request.prior_output.encode()
        ).hexdigest()[:12]
        raw = f"{request.node_id}|{request.intent.value}|{request.reason}|{output_part}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _is_deduplicated(self, dedupe_key: str) -> bool:
        seen = self.ctx.get_workflow_memory("retry_dedupe_keys") or []
        return dedupe_key in seen

    def _mark_dedupe_key(self, dedupe_key: str) -> None:
        seen = self.ctx.get_workflow_memory("retry_dedupe_keys") or []
        seen.append(dedupe_key)
        self.ctx.set_workflow_memory("retry_dedupe_keys", seen[-50:])

    def _intent_counts(self) -> dict[str, int]:
        return self.ctx.get_workflow_memory("retry_intent_counts") or {}

    def _increment_intent_count(self, request: RetryRequest) -> None:
        counts = self._intent_counts()
        key = f"{request.node_id}:{request.intent.value}"
        counts[key] = int(counts.get(key, 0)) + 1
        self.ctx.set_workflow_memory("retry_intent_counts", counts)

    def _is_recovery_chain_exhausted(self, request: RetryRequest) -> bool:
        if request.intent not in (RetryIntent.RECOVERY, RetryIntent.REFLECTION):
            return False
        counts = self._intent_counts()
        key = f"{request.node_id}:{request.intent.value}"
        return int(counts.get(key, 0)) >= MAX_SAME_INTENT_RETRIES

    def _record_telemetry(
        self,
        request: RetryRequest,
        result: RetryCoordinatorResult,
    ) -> None:
        record = RetryTelemetryRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            execution_id=self.execution_id,
            node_id=request.node_id,
            intent=request.intent.value,
            retry_type=result.retry_type.value if result.retry_type else "none",
            allowed=result.allowed,
            reason=result.block_reason or request.reason,
            deduplicated=result.deduplicated,
            budget_remaining=(
                result.decision.budget_remaining if result.decision else 0
            ),
        )
        history = self.ctx.get_workflow_memory("retry_coordinator_telemetry") or []
        history.append({
            "timestamp": record.timestamp,
            "nodeId": record.node_id,
            "intent": record.intent,
            "retryType": record.retry_type,
            "allowed": record.allowed,
            "reason": record.reason,
            "deduplicated": record.deduplicated,
            "budgetRemaining": record.budget_remaining,
        })
        self.ctx.set_workflow_memory("retry_coordinator_telemetry", history[-50:])

        if self.execution_id not in self._telemetry:
            self._telemetry[self.execution_id] = []
        self._telemetry[self.execution_id].append(record)

    @classmethod
    def get_telemetry(cls, execution_id: str) -> list[RetryTelemetryRecord]:
        return list(cls._telemetry.get(execution_id, []))

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._telemetry.pop(execution_id, None)

    def get_replay_log(self) -> list[dict[str, Any]]:
        return self.ctx.get_workflow_memory("retry_coordinator_telemetry") or []
