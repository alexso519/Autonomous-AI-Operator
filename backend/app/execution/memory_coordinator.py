"""
Unified memory coordinator — central registry and retrieval API.

Adapts existing memory systems without removing them:
  - HierarchicalMemory
  - ResearchMemory
  - ReasoningMemory
  - CognitiveMemoryStore
  - ContextManager (workflow/shared)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.cognition.cognitive_memory_store import CognitiveMemoryStore
from app.cognition.reasoning_memory import ReasoningMemory
from app.execution.context_manager import ContextManager
from app.execution.hierarchical_memory import HierarchicalMemory
from app.execution.memory_ranker import MemoryRanker, MemoryRecord, RankedMemory
from app.execution.memory_telemetry import MemoryTelemetry, RetrievalTrace
from app.execution.research_memory import ResearchMemory
from app.execution.retrieval_planner import (
    NS_COGNITIVE,
    NS_EVIDENCE,
    NS_HIERARCHICAL,
    NS_REASONING,
    NS_RESEARCH,
    NS_SEMANTIC,
    NS_SHARED,
    NS_TOOL,
    NS_WORKFLOW,
    RetrievalPlan,
)

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

# Keys from workflow memory surfaced in shared namespace
SHARED_MEMORY_KEYS = (
    "findings",
    "key_findings",
    "objective",
    "last_confidence",
    "research_context_block",
    "cognitive_deliberation",
)


def _is_retry_agent(name: str) -> bool:
    lower = name.lower()
    return lower.startswith("retry ") or " retry " in lower


class MemoryCoordinator:
    """
    Central memory registry with unified retrieval, deduplication,
    and source attribution.
    """

    def __init__(
        self,
        ctx: ContextManager,
        execution_id: str = "",
        telemetry: MemoryTelemetry | None = None,
    ) -> None:
        self._ctx = ctx
        self.execution_id = execution_id or ctx.execution_id or ""
        self.telemetry = telemetry or MemoryTelemetry.for_execution(self.execution_id)
        self._hierarchical = HierarchicalMemory(ctx)
        self._research = ResearchMemory(ctx)
        self._reasoning = ReasoningMemory(ctx)
        self._last_trace: RetrievalTrace | None = None

    @property
    def context_manager(self) -> ContextManager:
        return self._ctx

    def register_output(
        self,
        node_id: str,
        agent_name: str,
        output: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write-through to ContextManager (backward compatible)."""
        self._ctx.add_output(node_id, agent_name, output, metadata)

    def retrieve_sync(
        self,
        plan: RetrievalPlan,
        *,
        objective: str = "",
    ) -> list[RankedMemory]:
        """Synchronous retrieval (excludes async cognitive namespace)."""
        start = time.monotonic()
        trace_id = uuid.uuid4().hex[:12]

        records: list[MemoryRecord] = []
        for ns in plan.namespaces:
            if ns in (NS_COGNITIVE, NS_SEMANTIC):
                continue
            limit = plan.limits.get(ns, 10)
            records.extend(self._retrieve_namespace_sync(ns, limit, objective))

        return self._finalize_retrieval(
            plan, records, trace_id, (time.monotonic() - start) * 1000
        )

    async def retrieve(
        self,
        plan: RetrievalPlan,
        *,
        objective: str = "",
        emit_fn: EmitFn | None = None,
    ) -> list[RankedMemory]:
        """
        Unified retrieval: collect → dedupe → rank → trace.

        Returns ranked memories ready for context assembly.
        """
        start = time.monotonic()
        trace_id = uuid.uuid4().hex[:12]

        await self.telemetry.retrieval_strategy_selected(
            strategy=plan.strategy.value,
            namespaces=plan.namespaces,
            reason=plan.reason,
            emit_fn=emit_fn,
        )

        records: list[MemoryRecord] = []
        for ns in plan.namespaces:
            limit = plan.limits.get(ns, 10)
            if ns == NS_COGNITIVE:
                records.extend(await self._adapt_cognitive(limit, objective))
            elif ns == NS_SEMANTIC:
                records.extend(await self._adapt_semantic(limit, objective))
            else:
                records.extend(self._retrieve_namespace_sync(ns, limit, objective))

        ranked = self._finalize_retrieval(
            plan, records, trace_id, (time.monotonic() - start) * 1000
        )

        if self._last_trace:
            await self.telemetry.memory_retrieved(self._last_trace, emit_fn)
            await self.telemetry.memory_ranked(
                trace_id,
                len(ranked),
                MemoryRanker.top_reasons(ranked),
                emit_fn,
            )
            dupe_count = self._last_trace.records_retrieved - self._last_trace.records_after_dedup
            if dupe_count > 0:
                await self.telemetry.retrieval_conflict_detected(
                    "duplicate_content",
                    [f"removed_{dupe_count}_duplicates"],
                    emit_fn,
                )

        return ranked

    def _finalize_retrieval(
        self,
        plan: RetrievalPlan,
        records: list[MemoryRecord],
        trace_id: str,
        duration_ms: float,
    ) -> list[RankedMemory]:
        deduped, _dupe_count = MemoryRanker.dedupe(records)
        is_synthesis = plan.strategy.value == "synthesis"
        ranked = MemoryRanker.rank(
            deduped,
            is_synthesis=is_synthesis,
            limit=sum(plan.limits.values()),
        )
        if plan.deprioritize_retries:
            ranked = [r for r in ranked if not r.record.is_retry] + [
                r for r in ranked if r.record.is_retry
            ]
        trace = RetrievalTrace(
            trace_id=trace_id,
            strategy=plan.strategy.value,
            namespaces_requested=list(plan.namespaces),
            records_retrieved=len(records),
            records_after_dedup=len(deduped),
            records_after_rank=len(ranked),
            duration_ms=duration_ms,
            ranking_reasons=MemoryRanker.top_reasons(ranked),
        )
        self._last_trace = trace
        return ranked

    def _retrieve_namespace_sync(
        self,
        namespace: str,
        limit: int,
        objective: str,
    ) -> list[MemoryRecord]:
        return self._retrieve_namespace_impl(namespace, limit, objective)

    async def _retrieve_namespace(
        self,
        namespace: str,
        limit: int,
        objective: str,
    ) -> list[MemoryRecord]:
        if namespace == NS_COGNITIVE:
            return await self._adapt_cognitive(limit, objective)
        return self._retrieve_namespace_impl(namespace, limit, objective)

    def _retrieve_namespace_impl(
        self,
        namespace: str,
        limit: int,
        objective: str,
    ) -> list[MemoryRecord]:

        if namespace == NS_WORKFLOW:
            return self._adapt_workflow_outputs(limit)
        if namespace == NS_HIERARCHICAL:
            return self._adapt_hierarchical(limit)
        if namespace == NS_RESEARCH:
            return self._adapt_research(limit)
        if namespace == NS_REASONING:
            return self._adapt_reasoning(limit)
        if namespace == NS_SHARED:
            return self._adapt_shared(limit)
        if namespace == NS_TOOL:
            return self._adapt_tool_results(limit)
        if namespace == NS_EVIDENCE:
            return self._adapt_evidence(limit)
        if namespace == NS_SEMANTIC:
            return []
        return []

    def _adapt_workflow_outputs(self, limit: int) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        outputs = self._ctx.get_state().get("outputs") or {}
        items = list(outputs.items())[-limit:]
        for node_id, entry in items:
            agent_name = entry.get("agent_name", "Agent")
            output = entry.get("output", "")
            meta = entry.get("metadata") or {}
            conf = float(meta.get("confidence", 0.5))
            records.append(
                MemoryRecord(
                    id=f"wf:{node_id}",
                    namespace=NS_WORKFLOW,
                    content=f"[{agent_name}]: {output}",
                    source=f"workflow_output:{node_id}",
                    confidence=conf,
                    is_retry=_is_retry_agent(agent_name),
                    is_synthesis_relevant=True,
                    timestamp=meta.get("timestamp", ""),
                    metadata={"nodeId": node_id, "agentName": agent_name},
                    section="agent_outputs",
                )
            )
        return records

    def _adapt_hierarchical(self, limit: int) -> list[MemoryRecord]:
        block = self._hierarchical.get_context_block(max_summaries=limit)
        if not block.strip():
            return []
        return [
            MemoryRecord(
                id="hier:context_block",
                namespace=NS_HIERARCHICAL,
                content=block,
                source="hierarchical_memory",
                confidence=0.6,
                section="memory_tiers",
            )
        ]

    def _adapt_research(self, limit: int) -> list[MemoryRecord]:
        block = (
            self._ctx.get_workflow_memory("research_context_block")
            or self._research.get_context_block()
        )
        if not block.strip():
            return []
        return [
            MemoryRecord(
                id="research:context_block",
                namespace=NS_RESEARCH,
                content=block,
                source="research_memory",
                confidence=0.75,
                is_evidence=True,
                is_synthesis_relevant=True,
                section="research",
            )
        ]

    def _adapt_reasoning(self, limit: int) -> list[MemoryRecord]:
        state = self._reasoning.get_state()
        parts: list[str] = []
        consensus = state.get("consensus")
        if consensus:
            parts.append(f"Consensus: {consensus.get('statement', '')[:400]}")
        latest_unc = state.get("latestUncertainty")
        if latest_unc:
            parts.append(f"Uncertainty: {latest_unc.get('level', '')}")
        hyps = state.get("hypotheses") or []
        for h in hyps[-limit:]:
            parts.append(f"Hypothesis: {h.get('statement', h.get('text', ''))[:200]}")
        if not parts:
            return []
        return [
            MemoryRecord(
                id="reasoning:state",
                namespace=NS_REASONING,
                content="\n".join(parts),
                source="reasoning_memory",
                confidence=0.65,
                section="reasoning",
            )
        ]

    def _adapt_shared(self, limit: int) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        for key in SHARED_MEMORY_KEYS[:limit]:
            val = self._ctx.get_workflow_memory(key)
            if val is None:
                continue
            if isinstance(val, (dict, list)):
                text = json.dumps(val, ensure_ascii=False)[:500]
            else:
                text = str(val)[:500]
            records.append(
                MemoryRecord(
                    id=f"shared:{key}",
                    namespace=NS_SHARED,
                    content=f"- {key}: {text}",
                    source=f"workflow_memory:{key}",
                    confidence=0.55,
                    section="shared_memory",
                )
            )
        return records

    def _adapt_tool_results(self, limit: int) -> list[MemoryRecord]:
        tool_calls = self._ctx.get_workflow_memory("tool_calls") or []
        if not isinstance(tool_calls, list):
            return []
        records: list[MemoryRecord] = []
        for call in tool_calls[-limit:]:
            if call.get("status") != "completed":
                continue
            name = call.get("tool_name", "unknown")
            output = call.get("output") or {}
            records.append(
                MemoryRecord(
                    id=f"tool:{call.get('id', call.get('invocation_id', name))}",
                    namespace=NS_TOOL,
                    content=f"Tool {name}: {json.dumps(output, ensure_ascii=False)[:400]}",
                    source=f"tool_call:{name}",
                    confidence=0.9,
                    is_evidence=True,
                    is_synthesis_relevant=True,
                    timestamp=call.get("timestamp", ""),
                    section="tool_results",
                )
            )
        return records

    def _adapt_evidence(self, limit: int) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        evidence = self._ctx.get_workflow_memory("evidence_graph") or {}
        if evidence:
            claims = evidence.get("claims") or evidence.get("nodes") or []
            for claim in (claims if isinstance(claims, list) else [])[:limit]:
                text = claim.get("text") or claim.get("label") or str(claim)
                records.append(
                    MemoryRecord(
                        id=f"evidence:{claim.get('id', len(records))}",
                        namespace=NS_EVIDENCE,
                        content=str(text)[:400],
                        source="evidence_graph",
                        confidence=float(claim.get("score", 0.7)),
                        is_evidence=True,
                        is_synthesis_relevant=True,
                        section="evidence",
                    )
                )

        citations = self._ctx.get_workflow_memory("citations") or {}
        cite_list = citations.get("citations") or []
        for cite in cite_list[:limit]:
            records.append(
                MemoryRecord(
                    id=f"cite:{cite.get('id', len(records))}",
                    namespace=NS_EVIDENCE,
                    content=f"[{cite.get('index', '?')}] {cite.get('title', '')}: "
                    f"{cite.get('snippet', '')[:200]}",
                    source="citation_manager",
                    confidence=0.85,
                    is_evidence=True,
                    is_synthesis_relevant=True,
                    section="evidence",
                )
            )
        return records

    async def _adapt_cognitive(
        self, limit: int, objective: str
    ) -> list[MemoryRecord]:
        if not objective.strip():
            return []
        try:
            memories = await CognitiveMemoryStore.retrieve(objective, limit=limit)
        except Exception as exc:
            logger.warning("Cognitive memory retrieval failed: %s", exc)
            return []

        records: list[MemoryRecord] = []
        for m in memories:
            content = m.get("content") or {}
            text = json.dumps(content, ensure_ascii=False)[:600]
            records.append(
                MemoryRecord(
                    id=f"cog:{m.get('id', '')}",
                    namespace=NS_COGNITIVE,
                    content=text,
                    source=f"cognitive:{m.get('memoryType', '')}",
                    confidence=float(m.get("retrievalScore", 0.5)),
                    is_synthesis_relevant=True,
                    section="cognitive",
                    metadata={"memoryType": m.get("memoryType")},
                )
            )
        return records

    async def _adapt_semantic(
        self, limit: int, objective: str
    ) -> list[MemoryRecord]:
        if not objective.strip():
            return []
        try:
            from app.intelligence.semantic_retriever import SemanticRetriever

            memories = await SemanticRetriever.retrieve_for_objective(
                objective,
                execution_id=self.execution_id,
                limit=limit,
            )
        except Exception as exc:
            logger.warning("Semantic memory retrieval failed: %s", exc)
            return []

        records: list[MemoryRecord] = []
        for m in memories:
            records.append(
                MemoryRecord(
                    id=f"sem:{m.get('id', '')}",
                    namespace=NS_SEMANTIC,
                    content=(m.get("content") or "")[:600],
                    source=f"vector_memory:{m.get('memoryType', '')}",
                    confidence=float(m.get("combinedScore", m.get("similarity", 0.5))),
                    is_synthesis_relevant=True,
                    section="semantic",
                    metadata={"memoryType": m.get("memoryType")},
                )
            )
        return records

    def get_last_trace(self) -> RetrievalTrace | None:
        return self._last_trace

    def sync_hierarchical(self) -> None:
        """Sync tool calls into hierarchical memory tier."""
        self._hierarchical.sync_from_context()

    def record_execution_artifact(
        self,
        node_id: str,
        agent_name: str,
        output: str,
        confidence: float | None = None,
    ) -> None:
        self._hierarchical.record_execution(
            node_id, agent_name, output, confidence=confidence
        )
        if output:
            self._hierarchical.summarize_to_long_term(agent_name, output)
