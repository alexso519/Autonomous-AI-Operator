"""
Source ranking, deduplication, and evidence scoring for web research.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class RankedSource:
    id: str
    title: str
    url: str
    snippet: str
    domain: str
    relevance_score: float
    authority_score: float
    evidence_score: float
    duplicate_of: str | None = None
    fetch_text: str = ""
    citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "domain": self.domain,
            "relevanceScore": round(self.relevance_score, 3),
            "authorityScore": round(self.authority_score, 3),
            "evidenceScore": round(self.evidence_score, 3),
            "duplicateOf": self.duplicate_of,
            "hasFetch": bool(self.fetch_text),
        }


# Domains treated as higher authority for tech/finance research
_AUTHORITY_HINTS: dict[str, float] = {
    "nvidia.com": 0.95,
    "sec.gov": 0.92,
    "reuters.com": 0.88,
    "bloomberg.com": 0.88,
    "wsj.com": 0.85,
    "ft.com": 0.85,
    "arxiv.org": 0.82,
    "github.com": 0.75,
    "wikipedia.org": 0.7,
}


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.split("?")[0].rstrip("/"))
    return f"{parsed.netloc.lower()}{parsed.path.lower()}"


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _tokenize(text: str) -> set[str]:
    return {
        t.lower()
        for t in re.findall(r"[a-z0-9]{3,}", text.lower())
        if len(t) >= 3
    }


def _relevance(query: str, title: str, snippet: str) -> float:
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.5
    combined = f"{title} {snippet}".lower()
    hits = sum(1 for t in q_tokens if t in combined)
    return min(1.0, hits / max(len(q_tokens), 1) + 0.15)


def _authority(domain: str) -> float:
    for hint, score in _AUTHORITY_HINTS.items():
        if hint in domain:
            return score
    if domain.endswith(".edu") or domain.endswith(".gov"):
        return 0.8
    return 0.45


def _snippet_similarity(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


class SourceRanker:
    """Aggregate, dedupe, rank, and score web sources."""

    @classmethod
    def rank_search_results(
        cls,
        query: str,
        results: list[dict[str, str]],
        *,
        max_sources: int = 8,
    ) -> list[RankedSource]:
        ranked: list[RankedSource] = []
        seen_urls: dict[str, str] = {}

        for i, hit in enumerate(results):
            url = (hit.get("url") or "").strip()
            title = (hit.get("title") or "Untitled").strip()
            snippet = (hit.get("snippet") or "").strip()
            norm = _normalize_url(url)
            dom = _domain(url)

            duplicate_of: str | None = None
            for existing_norm, existing_id in seen_urls.items():
                if norm and existing_norm and norm == existing_norm:
                    duplicate_of = existing_id
                    break
                if not duplicate_of and snippet:
                    for prev in ranked:
                        if _snippet_similarity(snippet, prev.snippet) > 0.72:
                            duplicate_of = prev.id
                            break

            rel = _relevance(query, title, snippet)
            auth = _authority(dom)
            evidence = rel * 0.55 + auth * 0.45
            if duplicate_of:
                evidence *= 0.35

            source_id = f"src-{i}"
            if norm and not duplicate_of:
                seen_urls[norm] = source_id

            ranked.append(
                RankedSource(
                    id=source_id,
                    title=title,
                    url=url,
                    snippet=snippet,
                    domain=dom,
                    relevance_score=rel,
                    authority_score=auth,
                    evidence_score=evidence,
                    duplicate_of=duplicate_of,
                )
            )

        ranked.sort(key=lambda s: s.evidence_score, reverse=True)
        return ranked[:max_sources]

    @classmethod
    def collapse_duplicates(cls, sources: list[RankedSource]) -> list[RankedSource]:
        """Return primary sources only (non-duplicates), preserving order."""
        primaries = [s for s in sources if not s.duplicate_of]
        return primaries if primaries else sources[:5]

    @classmethod
    def attach_fetch_content(
        cls,
        sources: list[RankedSource],
        url: str,
        text: str,
    ) -> None:
        norm = _normalize_url(url)
        for src in sources:
            if _normalize_url(src.url) == norm or src.url == url:
                src.fetch_text = text[:8000]
                if text:
                    src.evidence_score = min(1.0, src.evidence_score + 0.12)
                break

    @classmethod
    def score_evidence_text(cls, query: str, text: str) -> float:
        if not text.strip():
            return 0.0
        return _relevance(query, "", text[:2000])
