"""
Unified capability registry — single source of truth for runtime capabilities.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class CapabilityHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass
class CapabilityPolicy:
    """Allow/deny and concurrency policy for a capability."""

    allowed: bool = True
    max_concurrent: int = 4
    requires_approval: bool = False
    local_model_friendly: bool = True
    estimated_cost_units: float = 1.0
    deny_reason: str = ""


@dataclass
class CapabilityRuntimeBinding:
    """Runtime binding connecting a capability to its executor adapter."""

    capability_id: str
    adapter_name: str
    handler: Callable[..., Awaitable[Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityDefinition:
    """Definition of a single runtime capability."""

    id: str
    name: str
    task_types: list[str]
    dependencies: list[str] = field(default_factory=list)
    policy: CapabilityPolicy = field(default_factory=CapabilityPolicy)
    description: str = ""
    health: CapabilityHealth = CapabilityHealth.HEALTHY
    failure_count: int = 0
    last_used_at: str = ""
    local_model_score: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "taskTypes": self.task_types,
            "dependencies": self.dependencies,
            "health": self.health.value,
            "localModelScore": self.local_model_score,
            "estimatedCost": self.policy.estimated_cost_units,
            "allowed": self.policy.allowed,
            "failureCount": self.failure_count,
        }


# Default capability catalog
_BUILTIN_CAPABILITIES: list[CapabilityDefinition] = [
    CapabilityDefinition("research", "Research", ["research", "investigation"], ["web_search"], local_model_score=0.7),
    CapabilityDefinition("web_search", "Web Search", ["search", "research"], local_model_score=0.95),
    CapabilityDefinition("webpage_fetch", "Webpage Fetch", ["fetch", "research"], ["web_search"], local_model_score=0.95),
    CapabilityDefinition("coding", "Coding", ["coding", "development"], ["terminal"], local_model_score=0.6),
    CapabilityDefinition("terminal", "Terminal", ["shell", "coding"], local_model_score=0.85),
    CapabilityDefinition("browser", "Browser", ["browser", "automation"], local_model_score=0.5),
    CapabilityDefinition("reasoning", "Reasoning", ["reasoning", "analysis"], local_model_score=0.75),
    CapabilityDefinition("synthesis", "Synthesis", ["synthesis", "report"], ["research"], local_model_score=0.8),
    CapabilityDefinition("retry", "Retry", ["retry", "recovery"], local_model_score=1.0),
    CapabilityDefinition("reflection", "Reflection", ["reflection", "quality"], local_model_score=0.85),
    CapabilityDefinition("memory", "Memory", ["memory", "retrieval"], local_model_score=0.95),
    CapabilityDefinition("benchmarking", "Benchmarking", ["benchmark"], local_model_score=0.9),
    CapabilityDefinition("cognition", "Cognition", ["cognition", "debate"], ["reasoning"], local_model_score=0.65),
    CapabilityDefinition("planning", "Planning", ["planning", "replan"], local_model_score=0.8),
    CapabilityDefinition("tool_execution", "Tool Execution", ["tool"], local_model_score=0.9),
    CapabilityDefinition("evaluation", "Evaluation", ["evaluation", "quality"], local_model_score=0.85),
]


class CapabilityRegistry:
    """
    Thread-safe capability registry with dependency graph and policy enforcement.
    """

    _global_lock = threading.Lock()
    _instances: dict[str, CapabilityRegistry] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._bindings: dict[str, CapabilityRuntimeBinding] = {}
        self._async_lock = asyncio.Lock()
        self._emit_fn: EmitFn | None = None

        for cap in _BUILTIN_CAPABILITIES:
            self._capabilities[cap.id] = cap

    @classmethod
    def for_execution(cls, execution_id: str) -> CapabilityRegistry:
        with cls._global_lock:
            if execution_id not in cls._instances:
                cls._instances[execution_id] = cls(execution_id)
            return cls._instances[execution_id]

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        with cls._global_lock:
            cls._instances.pop(execution_id, None)

    def set_emit_fn(self, emit_fn: EmitFn) -> None:
        self._emit_fn = emit_fn

    async def register(
        self,
        definition: CapabilityDefinition,
        binding: CapabilityRuntimeBinding | None = None,
    ) -> None:
        async with self._async_lock:
            self._capabilities[definition.id] = definition
            if binding:
                self._bindings[definition.id] = binding

        if self._emit_fn:
            await self._emit_fn(
                self.execution_id,
                "capability_registered",
                "system",
                f"Capability registered: {definition.name}",
                capabilityId=definition.id,
                capability=definition.to_dict(),
            )

    def get(self, capability_id: str) -> CapabilityDefinition | None:
        return self._capabilities.get(capability_id)

    def list_all(self) -> list[CapabilityDefinition]:
        return list(self._capabilities.values())

    def lookup_by_task_type(self, task_type: str) -> list[CapabilityDefinition]:
        task_lower = task_type.lower()
        matches = [
            c for c in self._capabilities.values()
            if task_lower in [t.lower() for t in c.task_types] or task_lower == c.id
        ]
        return sorted(matches, key=lambda c: c.local_model_score, reverse=True)

    async def select(
        self,
        task_type: str,
        *,
        prefer_local: bool = True,
    ) -> CapabilityDefinition | None:
        candidates = self.lookup_by_task_type(task_type)
        if not candidates:
            if self._emit_fn:
                await self._emit_fn(
                    self.execution_id,
                    "capability_unavailable",
                    "system",
                    f"No capability for task type: {task_type}",
                    taskType=task_type,
                )
            return None

        for cap in candidates:
            if not cap.policy.allowed:
                continue
            if cap.health == CapabilityHealth.UNAVAILABLE:
                continue
            if prefer_local and cap.local_model_score < 0.3:
                continue
            deps_ok = all(
                self._capabilities.get(d, CapabilityDefinition(d, d, [])).health
                != CapabilityHealth.UNAVAILABLE
                for d in cap.dependencies
            )
            if not deps_ok:
                continue

            cap.last_used_at = datetime.now(timezone.utc).isoformat()
            if self._emit_fn:
                await self._emit_fn(
                    self.execution_id,
                    "capability_selected",
                    "system",
                    f"Selected capability: {cap.name}",
                    capabilityId=cap.id,
                    taskType=task_type,
                    localModelScore=cap.local_model_score,
                )
            return cap

        if self._emit_fn:
            await self._emit_fn(
                self.execution_id,
                "capability_unavailable",
                "system",
                f"All candidates unavailable for: {task_type}",
                taskType=task_type,
            )
        return None

    def dependency_graph(self) -> dict[str, list[str]]:
        return {c.id: list(c.dependencies) for c in self._capabilities.values()}

    def estimate_cost(self, capability_ids: list[str]) -> float:
        total = 0.0
        for cid in capability_ids:
            cap = self._capabilities.get(cid)
            if cap:
                total += cap.policy.estimated_cost_units
        return total

    def local_model_suitability(self, capability_id: str) -> float:
        cap = self._capabilities.get(capability_id)
        return cap.local_model_score if cap else 0.0

    def record_failure(self, capability_id: str) -> None:
        cap = self._capabilities.get(capability_id)
        if not cap:
            return
        cap.failure_count += 1
        if cap.failure_count >= 3:
            cap.health = CapabilityHealth.DEGRADED
        if cap.failure_count >= 6:
            cap.health = CapabilityHealth.UNAVAILABLE

    def record_success(self, capability_id: str) -> None:
        cap = self._capabilities.get(capability_id)
        if not cap:
            return
        cap.failure_count = max(0, cap.failure_count - 1)
        if cap.failure_count < 3:
            cap.health = CapabilityHealth.HEALTHY

    def get_binding(self, capability_id: str) -> CapabilityRuntimeBinding | None:
        return self._bindings.get(capability_id)

    def stats(self) -> dict[str, Any]:
        caps = self.list_all()
        return {
            "total": len(caps),
            "healthy": sum(1 for c in caps if c.health == CapabilityHealth.HEALTHY),
            "degraded": sum(1 for c in caps if c.health == CapabilityHealth.DEGRADED),
            "unavailable": sum(1 for c in caps if c.health == CapabilityHealth.UNAVAILABLE),
        }
