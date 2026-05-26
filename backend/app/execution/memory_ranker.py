"""
Memory ranking with inspectable, deterministic scoring.

Stable ordering for replay support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class MemoryRecord:
    """Normalized memory item from any namespace."""

    id: str
    namespace: str
    content: str
    source: str
    confidence: float = 0.5
    recency_score: float = 0.5
    is_evidence: bool = False
    is_retry: bool = False
    is_hallucination_risk: bool = False
    is_synthesis_relevant: bool = False
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    section: str = "general"


@dataclass
class RankedMemory:
    """Memory record with ranking score and inspectable reasons."""

    record: MemoryRecord
    score: float
    reasons: list[str]


def _parse_timestamp(ts: str) -> float:
    if not ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


def _recency_weight(timestamp: str, now: float | None = None) -> float:
    """Higher score for more recent items (0..1)."""
    ts = _parse_timestamp(timestamp)
    if ts <= 0:
        return 0.3
    now = now or datetime.now(timezone.utc).timestamp()
    age_hours = max(0.0, (now - ts) / 3600.0)
    if age_hours < 1:
        return 1.0
    if age_hours < 24:
        return 0.8
    if age_hours < 168:
        return 0.5
    return 0.2


class MemoryRanker:
    """
    Deterministic memory ranking.

    Tie-breakers: namespace priority, then id (stable replay).
    """

    NAMESPACE_PRIORITY = {
        "evidence": 0,
        "tool_results": 1,
        "research": 2,
        "workflow_outputs": 3,
        "hierarchical": 4,
        "reasoning": 5,
        "cognitive": 6,
        "shared": 7,
        "general": 8,
    }

    @classmethod
    def score_record(
        cls,
        record: MemoryRecord,
        *,
        is_synthesis: bool = False,
        now: float | None = None,
    ) -> RankedMemory:
        reasons: list[str] = []
        score = 0.0

        # Base confidence
        conf = max(0.0, min(1.0, record.confidence))
        score += conf * 0.30
        reasons.append(f"confidence={conf:.2f}")

        # Recency
        recency = record.recency_score if record.recency_score != 0.5 else _recency_weight(
            record.timestamp, now
        )
        score += recency * 0.20
        reasons.append(f"recency={recency:.2f}")

        # Evidence weighting
        if record.is_evidence:
            score += 0.25
            reasons.append("evidence_boost")

        # Synthesis importance
        if is_synthesis and record.is_synthesis_relevant:
            score += 0.15
            reasons.append("synthesis_relevant")

        # Retry penalty
        if record.is_retry:
            score -= 0.20
            reasons.append("retry_penalty")

        # Hallucination risk penalty
        if record.is_hallucination_risk:
            score -= 0.15
            reasons.append("hallucination_penalty")

        # Usefulness from metadata
        usefulness = float(record.metadata.get("usefulness", 0.5))
        score += usefulness * 0.10
        reasons.append(f"usefulness={usefulness:.2f}")

        return RankedMemory(record=record, score=round(score, 4), reasons=reasons)

    @classmethod
    def rank(
        cls,
        records: list[MemoryRecord],
        *,
        is_synthesis: bool = False,
        limit: int | None = None,
        now: float | None = None,
    ) -> list[RankedMemory]:
        """Rank records deterministically with stable tie-breaking."""
        ranked = [
            cls.score_record(r, is_synthesis=is_synthesis, now=now)
            for r in records
        ]

        def sort_key(rm: RankedMemory) -> tuple:
            ns_prio = cls.NAMESPACE_PRIORITY.get(rm.record.namespace, 99)
            return (-rm.score, ns_prio, rm.record.id)

        ranked.sort(key=sort_key)

        if limit is not None:
            ranked = ranked[:limit]

        return ranked

    @classmethod
    def top_reasons(cls, ranked: list[RankedMemory], n: int = 5) -> list[dict[str, Any]]:
        return [
            {
                "id": rm.record.id,
                "namespace": rm.record.namespace,
                "score": rm.score,
                "reasons": rm.reasons,
                "source": rm.record.source,
            }
            for rm in ranked[:n]
        ]

    @classmethod
    def dedupe_key(cls, record: MemoryRecord) -> str:
        """Content-based dedup key (normalized, namespace-agnostic)."""
        return record.content.strip().lower()[:200]

    @classmethod
    def dedupe(
        cls,
        records: list[MemoryRecord],
    ) -> tuple[list[MemoryRecord], int]:
        """Remove duplicate content, keeping first occurrence (stable order)."""
        seen: set[str] = set()
        unique: list[MemoryRecord] = []
        dupes = 0
        for rec in records:
            key = cls.dedupe_key(rec)
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            unique.append(rec)
        return unique, dupes
