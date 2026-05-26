"""
Evidence graph: claims, supporting/conflicting evidence, source confidence, tool provenance.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceNode:
    id: str
    source_id: str
    text: str
    tool_name: str
    confidence: float
    url: str = ""
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sourceId": self.source_id,
            "text": self.text[:400],
            "toolName": self.tool_name,
            "confidence": round(self.confidence, 3),
            "url": self.url,
            "title": self.title,
        }


@dataclass
class ClaimNode:
    id: str
    text: str
    supporting: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    confidence: float = 0.5
    provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text[:300],
            "supporting": self.supporting,
            "conflicting": self.conflicting,
            "confidence": round(self.confidence, 3),
            "provenance": self.provenance,
        }


@dataclass
class ConflictRecord:
    id: str
    claim_a: str
    claim_b: str
    topic: str
    resolution: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claimA": self.claim_a[:150],
            "claimB": self.claim_b[:150],
            "topic": self.topic,
            "resolution": self.resolution,
        }


_BULLISH = re.compile(
    r"\b(growth|bullish|outperform|strong demand|positive outlook|buy)\b",
    re.I,
)
_BEARISH = re.compile(
    r"\b(decline|bearish|underperform|risk|negative outlook|sell|concern)\b",
    re.I,
)


class EvidenceGraph:
    """In-memory evidence graph for a single execution."""

    def __init__(self) -> None:
        self.evidence: dict[str, EvidenceNode] = {}
        self.claims: dict[str, ClaimNode] = {}
        self.conflicts: list[ConflictRecord] = []

    def add_evidence(
        self,
        source_id: str,
        text: str,
        tool_name: str,
        confidence: float,
        *,
        url: str = "",
        title: str = "",
    ) -> EvidenceNode:
        eid = f"ev-{uuid.uuid4().hex[:8]}"
        node = EvidenceNode(
            id=eid,
            source_id=source_id,
            text=text,
            tool_name=tool_name,
            confidence=confidence,
            url=url,
            title=title,
        )
        self.evidence[eid] = node
        return node

    def add_claim(
        self,
        text: str,
        evidence_ids: list[str] | None = None,
        provenance: list[str] | None = None,
    ) -> ClaimNode:
        cid = f"cl-{uuid.uuid4().hex[:8]}"
        ev_ids = evidence_ids or []
        conf = 0.35
        if ev_ids:
            scores = [
                self.evidence[e].confidence
                for e in ev_ids
                if e in self.evidence
            ]
            conf = sum(scores) / len(scores) if scores else 0.35

        claim = ClaimNode(
            id=cid,
            text=text,
            supporting=ev_ids,
            confidence=conf,
            provenance=provenance or [],
        )
        self.claims[cid] = claim
        return claim

    def detect_conflicts(self) -> list[ConflictRecord]:
        """Compare claims for directional disagreement."""
        claim_list = list(self.claims.values())
        found: list[ConflictRecord] = []
        for i, a in enumerate(claim_list):
            for b in claim_list[i + 1:]:
                if _BULLISH.search(a.text) and _BEARISH.search(b.text):
                    rec = ConflictRecord(
                        id=f"conf-{uuid.uuid4().hex[:6]}",
                        claim_a=a.text,
                        claim_b=b.text,
                        topic="directional_outlook",
                        resolution="Compare tool-backed sources; prefer higher evidence scores.",
                    )
                    found.append(rec)
                    a.conflicting.append(b.id)
                    b.conflicting.append(a.id)
                elif _BEARISH.search(a.text) and _BULLISH.search(b.text):
                    rec = ConflictRecord(
                        id=f"conf-{uuid.uuid4().hex[:6]}",
                        claim_a=a.text,
                        claim_b=b.text,
                        topic="directional_outlook",
                        resolution="Compare tool-backed sources; prefer higher evidence scores.",
                    )
                    found.append(rec)
                    a.conflicting.append(b.id)
                    b.conflicting.append(a.id)
        self.conflicts.extend(found)
        return found

    def extract_claims_from_text(self, text: str, source_evidence_ids: list[str]) -> list[ClaimNode]:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        created: list[ClaimNode] = []
        for sent in sentences:
            if len(sent.strip()) < 45:
                continue
            if re.search(r"\b(might|could|perhaps|uncertain)\b", sent, re.I):
                continue
            created.append(self.add_claim(sent.strip(), source_evidence_ids))
        return created

    def top_evidence(self, limit: int = 10) -> list[EvidenceNode]:
        return sorted(
            self.evidence.values(),
            key=lambda e: e.confidence,
            reverse=True,
        )[:limit]

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "evidence": [e.to_dict() for e in self.evidence.values()],
            "claims": [c.to_dict() for c in self.claims.values()],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "evidenceCount": len(self.evidence),
            "claimCount": len(self.claims),
            "conflictCount": len(self.conflicts),
        }

    def build_context_block(self, max_evidence: int = 6) -> str:
        lines = ["--- Evidence-backed research ---"]
        for ev in self.top_evidence(max_evidence):
            ref = f"[{ev.title or ev.source_id}]"
            lines.append(f"{ref} ({ev.tool_name}, conf {ev.confidence:.0%}): {ev.text[:280]}")
        if self.conflicts:
            lines.append("--- Source conflicts detected ---")
            for c in self.conflicts[:3]:
                lines.append(f"- {c.topic}: {c.resolution}")
        return "\n".join(lines)

    async def reconcile_with_knowledge_layer(
        self,
        topic: str,
        *,
        execution_id: str = "",
        emit_fn=None,
    ) -> dict[str, Any]:
        """Cluster evidence and reconcile conflicts via persistent knowledge layer."""
        from app.intelligence.evidence_reconciler import EvidenceReconciler
        from app.intelligence.research_synthesizer import ResearchSynthesizer

        claims = [
            {
                "text": c.text,
                "sourceId": c.id,
                "reliability": c.confidence,
            }
            for c in self.claims.values()
        ]
        consensus = await EvidenceReconciler.reconcile_claims(
            topic, claims, execution_id=execution_id, emit_fn=emit_fn,
        )
        findings = [{"text": c.text, "confidence": c.confidence} for c in self.claims.values()]
        gaps = await ResearchSynthesizer.detect_knowledge_gaps(topic, findings)
        return {
            "consensus": consensus,
            "knowledgeGaps": gaps,
            "conflictCount": len(self.conflicts),
        }
