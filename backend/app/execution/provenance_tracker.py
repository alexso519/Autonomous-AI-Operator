"""
Provenance tracking: link facts to tools and sources.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.execution.context_manager import ContextManager
from app.execution.evidence_graph import EvidenceGraph

EmitFn = Callable[..., Awaitable[None]]

PROVENANCE_KEY = "provenance_records"
TOOL_CHAINS_KEY = "tool_chains"


@dataclass
class ProvenanceRecord:
    id: str
    fact: str
    tool_name: str
    source_id: str
    source_url: str
    confidence: float
    citation_marker: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fact": self.fact[:250],
            "toolName": self.tool_name,
            "sourceId": self.source_id,
            "sourceUrl": self.source_url,
            "confidence": round(self.confidence, 3),
            "citationMarker": self.citation_marker,
        }


class ProvenanceTracker:
    """Persist provenance links in workflow memory and emit SSE events."""

    def __init__(self, ctx: ContextManager, graph: EvidenceGraph | None = None) -> None:
        self._ctx = ctx
        self._graph = graph
        records = ctx.get_workflow_memory(PROVENANCE_KEY)
        self._records: list[ProvenanceRecord] = []
        if isinstance(records, list):
            for r in records:
                if isinstance(r, dict):
                    self._records.append(
                        ProvenanceRecord(
                            id=r.get("id", ""),
                            fact=r.get("fact", ""),
                            tool_name=r.get("toolName", ""),
                            source_id=r.get("sourceId", ""),
                            source_url=r.get("sourceUrl", ""),
                            confidence=float(r.get("confidence", 0.5)),
                            citation_marker=r.get("citationMarker", ""),
                        )
                    )

    def link_fact(
        self,
        fact: str,
        tool_name: str,
        source_id: str,
        source_url: str,
        confidence: float,
        citation_marker: str = "",
    ) -> ProvenanceRecord:
        rec = ProvenanceRecord(
            id=f"prov-{uuid.uuid4().hex[:8]}",
            fact=fact,
            tool_name=tool_name,
            source_id=source_id,
            source_url=source_url,
            confidence=confidence,
            citation_marker=citation_marker,
        )
        self._records.append(rec)
        self._persist()
        if self._graph:
            self._graph.add_evidence(
                source_id=source_id,
                text=fact,
                tool_name=tool_name,
                confidence=confidence,
                url=source_url,
            )
        return rec

    def _persist(self) -> None:
        self._ctx.set_workflow_memory(
            PROVENANCE_KEY,
            [r.to_dict() for r in self._records[-200:]],
        )

    async def emit_linked(
        self,
        execution_id: str,
        record: ProvenanceRecord,
        emit_fn: EmitFn,
    ) -> None:
        await emit_fn(
            execution_id,
            "provenance_linked",
            "system",
            f"Linked fact to {record.tool_name}: {record.fact[:80]}",
            provenanceId=record.id,
            toolName=record.tool_name,
            sourceId=record.source_id,
            sourceUrl=record.source_url,
            confidence=record.confidence,
            citationMarker=record.citation_marker,
        )

    def all_records(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._records]

    def append_tool_chain(self, chain: dict[str, Any]) -> None:
        chains = self._ctx.get_workflow_memory(TOOL_CHAINS_KEY) or []
        if not isinstance(chains, list):
            chains = []
        chains.append(chain)
        self._ctx.set_workflow_memory(TOOL_CHAINS_KEY, chains[-30:])

    def evolve_source_reliability(self, ranked_sources: list[Any]) -> None:
        """Update per-source reliability scores from fetch success and evidence."""
        scores = self._ctx.get_workflow_memory("source_reliability") or {}
        if not isinstance(scores, dict):
            scores = {}
        for src in ranked_sources:
            sid = getattr(src, "id", None) or (src.get("id") if isinstance(src, dict) else None)
            if not sid:
                continue
            ev = float(getattr(src, "evidence_score", 0) or (src.get("evidenceScore", 0.5) if isinstance(src, dict) else 0.5))
            has_fetch = bool(getattr(src, "fetch_text", "") or (src.get("fetchText") if isinstance(src, dict) else ""))
            prior = float(scores.get(sid, 0.5))
            delta = 0.05 if has_fetch else -0.02
            scores[sid] = round(min(1.0, max(0.1, prior * 0.7 + (ev + delta) * 0.3)), 3)
        self._ctx.set_workflow_memory("source_reliability", scores)
