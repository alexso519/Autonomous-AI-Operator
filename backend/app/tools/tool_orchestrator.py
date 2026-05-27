"""
Autonomous tool orchestration: chains, dependencies, branching, caching, parallel tools.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.locale import t
from app.execution.context_manager import ContextManager
from app.execution.evidence_graph import EvidenceGraph
from app.execution.provenance_tracker import ProvenanceTracker
from app.execution.research_memory import ResearchMemory
from app.tools.builtin.web_search import execute_web_search
from app.tools.builtin.webpage_fetch import execute_webpage_fetch
from app.tools.citation_manager import CitationManager
from app.tools.source_ranker import RankedSource, SourceRanker
from app.tools.tool_efficiency import ToolEfficiencyLayer
from app.tools.tool_models import WebSearchInput, WebpageFetchInput, ToolExecutionError

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

MAX_FETCH_PARALLEL = 3
MAX_FETCH_URLS = 4


@dataclass
class ToolStep:
    tool_name: str
    input_data: dict[str, Any]
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str | None = None
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "toolName": self.tool_name,
            "inputData": self.input_data,
            "dependsOn": self.depends_on,
            "parallelGroup": self.parallel_group,
            "optional": self.optional,
        }


@dataclass
class ToolChainPlan:
    chain_id: str
    name: str
    steps: list[ToolStep]
    estimated_usefulness: float
    query: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chainId": self.chain_id,
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "estimatedUsefulness": round(self.estimated_usefulness, 3),
            "query": self.query,
        }


def _estimate_usefulness(query: str, is_research: bool) -> float:
    if not is_research:
        return 0.4
    score = 0.65
    if len(query.split()) >= 3:
        score += 0.1
    if any(k in query.lower() for k in ("invest", "nvidia", "market", "outlook")):
        score += 0.1
    return min(1.0, score)


def _summarize_sources(sources: list[RankedSource]) -> str:
    lines = []
    for s in SourceRanker.collapse_duplicates(sources)[:5]:
        lines.append(f"**{s.title}** ({s.domain}): {s.snippet[:200]}")
        if s.fetch_text:
            lines.append(f"  Detail: {s.fetch_text[:300]}...")
    return "\n".join(lines)


def _compare_viewpoints(sources: list[RankedSource]) -> dict[str, Any]:
    texts = [s.fetch_text or s.snippet for s in sources if not s.duplicate_of]
    bullish = sum(
        1 for t in texts
        if re.search(r"\b(growth|bullish|strong|positive|outperform)\b", t, re.I)
    )
    bearish = sum(
        1 for t in texts
        if re.search(r"\b(decline|bearish|risk|negative|underperform)\b", t, re.I)
    )
    return {
        "bullishSignals": bullish,
        "bearishSignals": bearish,
        "conflictLikely": bullish > 0 and bearish > 0,
        "summary": (
            f"{bullish} bullish signal(s), {bearish} bearish signal(s) across sources."
        ),
    }


class ToolOrchestrator:
    """
    Plan and execute multi-tool research chains with caching and provenance.
    """

    RESEARCH_CHAIN_NAME = "web_research_pipeline"

    @classmethod
    def plan_research_chain(cls, query: str) -> ToolChainPlan:
        steps = [
            ToolStep("web_search", {"query": query, "max_results": 6}),
            ToolStep(
                "webpage_fetch",
                {"parallel": True},
                depends_on=["web_search"],
                parallel_group="fetch",
            ),
            ToolStep("compare_sources", {}, depends_on=["webpage_fetch"]),
            ToolStep("summarize_evidence", {}, depends_on=["compare_sources"]),
        ]
        return ToolChainPlan(
            chain_id=f"chain-{uuid.uuid4().hex[:8]}",
            name=cls.RESEARCH_CHAIN_NAME,
            steps=steps,
            estimated_usefulness=_estimate_usefulness(query, True),
            query=query,
        )

    @classmethod
    def plan_tools_for_goal(cls, goal: str, objective: str = "") -> ToolChainPlan | None:
        combined = f"{goal} {objective}".lower()
        if not any(
            k in combined
            for k in ("research", "invest", "search", "find", "analyze", "outlook", "web")
        ):
            return None
        query = objective or goal
        return cls.plan_research_chain(query[:200])

    @classmethod
    def optimize_step_order(cls, steps: list[ToolStep]) -> list[ToolStep]:
        """Topological order by depends_on (step tool names as ids)."""
        ordered: list[ToolStep] = []
        remaining = list(steps)
        name_to_step = {s.tool_name: s for s in steps}
        done_names: set[str] = set()

        while remaining:
            progressed = False
            for step in list(remaining):
                deps_met = all(d in done_names for d in step.depends_on)
                if deps_met:
                    ordered.append(step)
                    done_names.add(step.tool_name)
                    remaining.remove(step)
                    progressed = True
            if not progressed:
                ordered.extend(remaining)
                break
        return ordered

    @classmethod
    async def execute_research_pipeline(
        cls,
        execution_id: str,
        query: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        node_id: str = "research-pipeline",
        agent_name: str = "ResearchScout",
    ) -> dict[str, Any]:
        """
        Full web intelligence pipeline:
        search → rank → fetch → cite → evidence graph → compare conflicts.
        """
        plan = cls.plan_research_chain(query)
        research_mem = ResearchMemory(ctx)
        research_mem.enable_research_mode(query)
        graph = EvidenceGraph()
        citations = CitationManager()
        provenance = ProvenanceTracker(ctx, graph)

        await emit_fn(
            execution_id,
            "tool_chain_started",
            agent_name,
            t("research_chain_started", name=plan.name),
            chainId=plan.chain_id,
            chainName=plan.name,
            steps=[s.to_dict() for s in plan.steps],
            estimatedUsefulness=plan.estimated_usefulness,
        )

        chain_results: dict[str, Any] = {
            "chainId": plan.chain_id,
            "query": query,
            "startedAt": datetime.now(timezone.utc).isoformat(),
            "steps": [],
        }

        # Step 1: web_search (with cache)
        search_input = {"query": query, "max_results": 6}
        cache = await ToolEfficiencyLayer.lookup_cache("web_search", search_input)
        if cache.hit and cache.output:
            search_out = cache.output
        else:
            try:
                search_out = execute_web_search(
                    WebSearchInput(query=query, max_results=6)
                )
                await ToolEfficiencyLayer.store_cache(
                    "web_search", search_input, search_out, execution_id
                )
            except ToolExecutionError as exc:
                search_out = {"query": query, "results": [], "error": str(exc)}

        chain_results["steps"].append({"tool": "web_search", "status": "completed"})

        # Rank & dedupe
        raw_results = search_out.get("results") or []
        ranked = SourceRanker.rank_search_results(query, raw_results)
        primaries = SourceRanker.collapse_duplicates(ranked)

        for src in primaries[:6]:
            await emit_fn(
                execution_id,
                "source_ranked",
                agent_name,
                t("source_ranked", title=src.title[:60], score=f"{src.evidence_score:.0%}"),
                sourceId=src.id,
                title=src.title,
                url=src.url,
                evidenceScore=src.evidence_score,
                relevanceScore=src.relevance_score,
                duplicateOf=src.duplicate_of,
            )

        research_mem.store_sources([s.to_dict() for s in ranked])

        # Parallel webpage_fetch
        fetch_targets = [
            s for s in primaries
            if s.url and s.url.startswith("http") and not s.duplicate_of
        ][:MAX_FETCH_URLS]

        async def _fetch_one(src: RankedSource) -> tuple[RankedSource, dict[str, Any] | None]:
            fin = {"url": src.url, "max_bytes": 50000, "timeout_seconds": 12}
            cached = await ToolEfficiencyLayer.lookup_cache("webpage_fetch", fin)
            if cached.hit and cached.output:
                return src, cached.output
            try:
                out = execute_webpage_fetch(WebpageFetchInput(**fin))
                await ToolEfficiencyLayer.store_cache(
                    "webpage_fetch", fin, out, execution_id
                )
                return src, out
            except Exception as exc:
                logger.debug("Fetch failed %s: %s", src.url, exc)
                return src, None

        if fetch_targets:
            sem = asyncio.Semaphore(MAX_FETCH_PARALLEL)

            async def _bounded(s: RankedSource):
                async with sem:
                    return await _fetch_one(s)

            fetched = await asyncio.gather(*[_bounded(s) for s in fetch_targets])

            for src, fout in fetched:
                if not fout:
                    continue
                text = fout.get("text") or ""
                SourceRanker.attach_fetch_content(ranked, src.url, text)
                cite = citations.add_from_source(
                    src.id, src.title, src.url, text[:400] or src.snippet
                )
                ev = graph.add_evidence(
                    src.id,
                    text[:500] or src.snippet,
                    "webpage_fetch",
                    src.evidence_score,
                    url=src.url,
                    title=src.title,
                )
                await emit_fn(
                    execution_id,
                    "evidence_collected",
                    agent_name,
                    t("evidence_collected", domain=src.domain, title=src.title[:50]),
                    evidenceId=ev.id,
                    sourceId=src.id,
                    url=src.url,
                    confidence=src.evidence_score,
                    toolName="webpage_fetch",
                )
                await emit_fn(
                    execution_id,
                    "citation_attached",
                    agent_name,
                    t("citation_attached", marker=cite.marker, title=src.title[:50]),
                    citationId=cite.id,
                    marker=cite.marker,
                    url=src.url,
                )
                rec = provenance.link_fact(
                    text[:200] or src.snippet,
                    "webpage_fetch",
                    src.id,
                    src.url,
                    src.evidence_score,
                    cite.marker,
                )
                await provenance.emit_linked(execution_id, rec, emit_fn)

        # Compare sources & conflicts
        comparison = _compare_sources(ranked)
        chain_results["comparison"] = comparison

        for src in primaries:
            if src.fetch_text or src.snippet:
                graph.extract_claims_from_text(
                    src.fetch_text or src.snippet,
                    [
                        e.id
                        for e in graph.evidence.values()
                        if e.source_id == src.id
                    ],
                )

        conflicts = graph.detect_conflicts()
        for conf in conflicts:
            await emit_fn(
                execution_id,
                "conflict_detected",
                agent_name,
                f"Conflict: {conf.topic} — {conf.resolution}",
                conflictId=conf.id,
                topic=conf.topic,
                claimA=conf.claim_a[:120],
                claimB=conf.claim_b[:120],
            )

        knowledge_reconciliation: dict[str, Any] = {}
        try:
            knowledge_reconciliation = await graph.reconcile_with_knowledge_layer(
                query, execution_id=execution_id, emit_fn=emit_fn,
            )
            from app.intelligence.research_synthesizer import ResearchSynthesizer

            findings = [
                {"text": c.text, "confidence": c.confidence}
                for c in graph.claims.values()
            ]
            gaps = await ResearchSynthesizer.detect_knowledge_gaps(query, findings)
            if gaps:
                chain_results["knowledgeGaps"] = gaps
                await emit_fn(
                    execution_id,
                    "log",
                    agent_name,
                    f"Knowledge gaps detected: {', '.join(gaps[:5])}",
                    gaps=gaps,
                )
            consolidated = await ResearchSynthesizer.consolidate_research(
                query, findings, execution_id=execution_id, emit_fn=emit_fn,
            )
            chain_results["researchConsolidation"] = consolidated
            provenance.evolve_source_reliability(ranked)
        except Exception as exc:
            logger.debug("Knowledge-layer research enrichment skipped: %s", exc)

        chain_results["knowledgeReconciliation"] = knowledge_reconciliation

        summary = _summarize_sources(ranked)
        graph.add_claim(
            f"Research summary for '{query}': {comparison.get('summary', '')}",
            provenance=["web_search", "webpage_fetch"],
        )

        research_mem.store_evidence_graph(graph)
        research_mem.store_citations(citations)
        provenance.append_tool_chain(chain_results)

        ctx.set_workflow_memory(
            "research_pipeline_summary",
            {
                "summary": summary,
                "comparison": comparison,
                "sourceCount": len(primaries),
                "citationCount": len(citations.all_citations()),
            },
        )
        ctx.set_workflow_memory(
            "research_context_block",
            graph.build_context_block() + "\n\n" + citations.format_bibliography(),
        )
        ctx.set_workflow_memory("research_pipeline_completed", True)

        chain_results["status"] = "completed"
        chain_results["completedAt"] = datetime.now(timezone.utc).isoformat()
        research_mem.append_tool_chain(chain_results)

        await emit_fn(
            execution_id,
            "tool_chain_completed",
            agent_name,
            f"Research chain complete — {len(primaries)} sources, {len(conflicts)} conflict(s)",
            chainId=plan.chain_id,
            sourceCount=len(primaries),
            conflictCount=len(conflicts),
            citationCount=len(citations.all_citations()),
        )

        return {
            "plan": plan.to_dict(),
            "rankedSources": [s.to_dict() for s in ranked],
            "summary": summary,
            "comparison": comparison,
            "graph": graph.to_snapshot(),
            "citations": citations.to_snapshot(),
        }
