"""
Research synthesizer — research consolidation and citation reconciliation.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.evidence_reconciler import EvidenceReconciler
from app.intelligence.knowledge_synthesizer import KnowledgeSynthesizer

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

CITATION_PATTERN = re.compile(r"\[(\d+)\]|\((https?://[^)]+)\)")


class ResearchSynthesizer:
    """Consolidate research outputs with evidence reconciliation."""

    @classmethod
    async def consolidate_research(
        cls,
        objective: str,
        findings: list[dict[str, Any]],
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"enabled": False}

        claims: list[dict[str, Any]] = []
        sources: list[str] = []
        for i, f in enumerate(findings):
            text = str(f.get("text", f.get("output", "")))
            if text:
                sources.append(text)
                claims.append({
                    "text": text,
                    "sourceId": f.get("sourceId", f"finding_{i}"),
                    "reliability": float(f.get("confidence", 0.65)),
                })

        consensus = await EvidenceReconciler.reconcile_claims(
            objective[:120], claims,
            execution_id=execution_id, emit_fn=emit_fn,
        )
        synthesis = await KnowledgeSynthesizer.synthesize_from_sources(
            objective[:120], sources,
            execution_id=execution_id,
            layer="research",
            emit_fn=emit_fn,
        )
        citations = cls._reconcile_citations(sources)

        return {
            "enabled": True,
            "consensus": consensus,
            "synthesis": synthesis,
            "citations": citations,
            "findingCount": len(findings),
        }

    @classmethod
    def _reconcile_citations(cls, sources: list[str]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        citations: list[dict[str, Any]] = []
        for i, src in enumerate(sources):
            for match in CITATION_PATTERN.finditer(src):
                ref = match.group(1) or match.group(2) or ""
                if ref in seen:
                    continue
                seen.add(ref)
                citations.append({"index": len(citations) + 1, "ref": ref, "sourceIndex": i})
        return citations

    @classmethod
    async def detect_knowledge_gaps(
        cls,
        objective: str,
        findings: list[dict[str, Any]],
    ) -> list[str]:
        """Identify topics mentioned in objective but missing from findings."""
        if not objective:
            return []
        keywords = [w for w in objective.lower().split() if len(w) > 5]
        found_text = " ".join(str(f.get("text", "")) for f in findings).lower()
        return [kw for kw in keywords[:10] if kw not in found_text]
