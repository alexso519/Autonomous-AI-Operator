"""
Research-specific memory: sources, citations, evidence snapshots, tool chains.
"""

from __future__ import annotations

from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.evidence_graph import EvidenceGraph
from app.tools.citation_manager import CitationManager

RESEARCH_KEY = "research_memory"


class ResearchMemory:
    """Tiered research artifacts stored in workflow memory."""

    def __init__(self, ctx: ContextManager) -> None:
        self._ctx = ctx
        if self._ctx.get_workflow_memory(RESEARCH_KEY) is None:
            self._ctx.set_workflow_memory(
                RESEARCH_KEY,
                {
                    "sources": [],
                    "citations": [],
                    "evidence": {},
                    "toolChains": [],
                    "researchMode": False,
                },
            )

    def _store(self) -> dict[str, Any]:
        return dict(self._ctx.get_workflow_memory(RESEARCH_KEY) or {})

    def enable_research_mode(self, query: str) -> None:
        data = self._store()
        data["researchMode"] = True
        data["query"] = query
        self._ctx.set_workflow_memory(RESEARCH_KEY, data)
        self._ctx.set_workflow_memory("autonomous_research_mode", True)

    def is_research_mode(self) -> bool:
        return bool(self._store().get("researchMode"))

    def store_sources(self, sources: list[dict[str, Any]]) -> None:
        data = self._store()
        data["sources"] = sources
        self._ctx.set_workflow_memory(RESEARCH_KEY, data)

    def store_evidence_graph(self, graph: EvidenceGraph) -> None:
        data = self._store()
        data["evidence"] = graph.to_snapshot()
        self._ctx.set_workflow_memory(RESEARCH_KEY, data)
        self._ctx.set_workflow_memory("evidence_graph", graph.to_snapshot())

    def store_citations(self, manager: CitationManager) -> None:
        data = self._store()
        snap = manager.to_snapshot()
        data["citations"] = snap.get("citations", [])
        data["claimLinks"] = snap.get("claimLinks", [])
        self._ctx.set_workflow_memory(RESEARCH_KEY, data)
        self._ctx.set_workflow_memory("citations", snap)

    def append_tool_chain(self, chain: dict[str, Any]) -> None:
        data = self._store()
        chains = list(data.get("toolChains") or [])
        chains.append(chain)
        data["toolChains"] = chains[-20:]
        self._ctx.set_workflow_memory(RESEARCH_KEY, data)

    def get_context_block(self) -> str:
        data = self._store()
        lines: list[str] = []
        query = data.get("query")
        if query:
            lines.append(f"Research query: {query}")

        sources = data.get("sources") or []
        for src in sources[:5]:
            if isinstance(src, dict) and not src.get("duplicateOf"):
                lines.append(
                    f"- [{src.get('title', '?')}] ({src.get('evidenceScore', 0):.0%}) "
                    f"{src.get('snippet', '')[:160]}"
                )

        evidence = data.get("evidence") or {}
        if evidence.get("conflicts"):
            lines.append("Conflicts between sources were detected — cite both sides carefully.")

        cites = data.get("citations") or []
        if cites:
            lines.append(f"{len(cites)} citation(s) available — use [n] markers when stating facts.")

        return "\n".join(lines).strip()
