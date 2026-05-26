"""
Semantic UI memory — visual pattern storage and similarity matching.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class UIPattern:
    pattern_id: str
    fingerprint: str
    semantic_type: str
    labels: list[str] = field(default_factory=list)
    element_count: int = 0
    visit_count: int = 1
    last_seen: str = field(default_factory=_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patternId": self.pattern_id,
            "fingerprint": self.fingerprint,
            "semanticType": self.semantic_type,
            "labels": self.labels[:10],
            "elementCount": self.element_count,
            "visitCount": self.visit_count,
            "lastSeen": self.last_seen,
        }


class SemanticUIMemory:
    """Visual pattern memory for UI similarity and adaptive grounding."""

    _patterns: dict[str, dict[str, UIPattern]] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        if execution_id not in self._patterns:
            self._patterns[execution_id] = {}

    @staticmethod
    def fingerprint(
        semantic_type: str,
        labels: list[str],
        element_count: int,
    ) -> str:
        key = f"{semantic_type}:{element_count}:{','.join(sorted(labels[:20]))}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def record(
        self,
        semantic_type: str,
        labels: list[str],
        element_count: int,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> UIPattern:
        fp = self.fingerprint(semantic_type, labels, element_count)
        store = self._patterns.setdefault(self.execution_id, {})
        if fp in store:
            pattern = store[fp]
            pattern.visit_count += 1
            pattern.last_seen = _now()
        else:
            pattern = UIPattern(
                pattern_id=f"pat-{fp[:8]}",
                fingerprint=fp,
                semantic_type=semantic_type,
                labels=labels[:20],
                element_count=element_count,
                metadata=metadata or {},
            )
            store[fp] = pattern
        return pattern

    def find_similar(
        self,
        semantic_type: str,
        labels: list[str],
        element_count: int,
        *,
        threshold: float = 0.5,
    ) -> list[tuple[UIPattern, float]]:
        fp = self.fingerprint(semantic_type, labels, element_count)
        store = self._patterns.get(self.execution_id, {})
        results: list[tuple[UIPattern, float]] = []

        label_set = set(l.lower() for l in labels)
        for pattern in store.values():
            if pattern.semantic_type != semantic_type:
                continue
            pat_labels = set(l.lower() for l in pattern.labels)
            overlap = len(label_set & pat_labels)
            union = len(label_set | pat_labels) or 1
            jaccard = overlap / union
            fp_match = 1.0 if pattern.fingerprint == fp else 0.0
            score = max(jaccard, fp_match * 0.9)
            if score >= threshold:
                results.append((pattern, score))
        results.sort(key=lambda x: -x[1])
        return results[:5]

    def to_dict(self) -> dict[str, Any]:
        store = self._patterns.get(self.execution_id, {})
        return {
            "executionId": self.execution_id,
            "patternCount": len(store),
            "patterns": [p.to_dict() for p in store.values()],
        }

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._patterns.pop(execution_id, None)
