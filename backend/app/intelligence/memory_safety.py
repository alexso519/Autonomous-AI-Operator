"""
Memory safety — TTL, deduplication, retrieval budgets, compaction.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config.settings import settings

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = (
    "password",
    "api_key",
    "secret",
    "token",
    "credential",
    "ssn",
    "credit card",
)


class MemorySafety:
    """Runtime safety constraints for persistent intelligence."""

    MAX_GRAPH_DEPTH = 5
    MAX_REASONING_HOPS = 4
    MAX_REASONING_CYCLES = 100
    MIN_INFERENCE_CONFIDENCE = 0.4
    MAX_OPEN_CONFLICTS = 8

    @classmethod
    def retrieval_budget(cls) -> int:
        return min(20, max(1, settings.max_long_term_memories // 50))

    @classmethod
    def reasoning_budget(cls) -> int:
        return min(15, max(3, settings.max_long_term_memories // 200))

    @classmethod
    def graph_traversal_depth_limit(cls) -> int:
        return min(cls.MAX_GRAPH_DEPTH, max(1, settings.max_long_term_memories // 1000))

    @classmethod
    def max_reasoning_hops(cls) -> int:
        return cls.MAX_REASONING_HOPS

    @classmethod
    def min_inference_confidence(cls) -> float:
        return max(
            cls.MIN_INFERENCE_CONFIDENCE,
            settings.world_model_confidence_threshold,
        )

    @classmethod
    def contradiction_escalation_limit(cls) -> int:
        return cls.MAX_OPEN_CONFLICTS

    @classmethod
    def detect_traversal_cycle(cls, visited: set[str], node_id: str) -> bool:
        if node_id in visited:
            return True
        if len(visited) >= cls.MAX_REASONING_CYCLES:
            return True
        return False

    @classmethod
    def knowledge_freshness_decay(cls, freshness: float, days_stale: int) -> float:
        if days_stale <= 0:
            return freshness
        decay = 0.98 ** min(days_stale, 90)
        return max(0.1, freshness * decay)

    @classmethod
    def suppress_hallucinated_fact(cls, confidence: float, has_evidence: bool) -> bool:
        """Suppress inferred facts without sufficient evidence."""
        if has_evidence:
            return confidence < cls.min_inference_confidence()
        return confidence < cls.min_inference_confidence() + 0.15

    @classmethod
    def should_deduplicate(cls) -> bool:
        return settings.enable_vector_memory

    @classmethod
    def compute_expiry(cls, created_at_iso: str) -> str | None:
        days = settings.memory_retention_days
        if days <= 0:
            return None
        try:
            created = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            expiry = created + timedelta(days=days)
            return expiry.isoformat()
        except ValueError:
            return None

    @classmethod
    def passes_privacy_filter(cls, content: str, privacy_tag: str = "internal") -> bool:
        if privacy_tag == "blocked":
            return False
        lower = content.lower()
        for pattern in SENSITIVE_PATTERNS:
            if pattern in lower:
                return False
        return True

    @classmethod
    def world_model_confidence_ok(cls, confidence: float) -> bool:
        return confidence >= settings.world_model_confidence_threshold

    @classmethod
    def heuristic_evolution_allowed(cls, current_score: float, proposed_score: float) -> bool:
        """Controlled heuristic evolution — limit large jumps."""
        delta = abs(proposed_score - current_score)
        return delta <= 0.3

    @classmethod
    async def run_compaction_job(cls) -> dict[str, int]:
        from app.intelligence.vector_memory import VectorMemory

        expired = await VectorMemory.compact_expired()
        return {"expiredMemoriesRemoved": expired}
