"""
Citation tracking and claim-to-source linking.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Citation:
    id: str
    source_id: str
    title: str
    url: str
    excerpt: str
    marker: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sourceId": self.source_id,
            "title": self.title,
            "url": self.url,
            "excerpt": self.excerpt[:300],
            "marker": self.marker,
        }

    def inline_ref(self) -> str:
        return f"[{self.marker}]"


@dataclass
class ClaimCitation:
    claim_id: str
    claim_text: str
    citation_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    unsupported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claimId": self.claim_id,
            "claimText": self.claim_text[:200],
            "citationIds": self.citation_ids,
            "confidence": round(self.confidence, 3),
            "unsupported": self.unsupported,
        }


class CitationManager:
    """Build and manage citations from ranked sources."""

    def __init__(self) -> None:
        self._citations: dict[str, Citation] = {}
        self._claim_links: list[ClaimCitation] = []
        self._marker_index = 0

    def add_from_source(
        self,
        source_id: str,
        title: str,
        url: str,
        excerpt: str,
    ) -> Citation:
        self._marker_index += 1
        marker = str(self._marker_index)
        cid = f"cite-{uuid.uuid4().hex[:8]}"
        citation = Citation(
            id=cid,
            source_id=source_id,
            title=title,
            url=url,
            excerpt=excerpt,
            marker=marker,
        )
        self._citations[cid] = citation
        return citation

    def link_claim(
        self,
        claim_text: str,
        citation_ids: list[str],
        confidence: float = 0.6,
    ) -> ClaimCitation:
        linked = [
            cid for cid in citation_ids if cid in self._citations
        ]
        unsupported = len(linked) == 0 and len(claim_text) > 30
        entry = ClaimCitation(
            claim_id=f"claim-{uuid.uuid4().hex[:8]}",
            claim_text=claim_text,
            citation_ids=linked,
            confidence=confidence if linked else 0.25,
            unsupported=unsupported,
        )
        self._claim_links.append(entry)
        return entry

    def format_bibliography(self) -> str:
        lines = ["## Sources", ""]
        for cite in sorted(
            self._citations.values(),
            key=lambda c: int(c.marker) if c.marker.isdigit() else 0,
        ):
            lines.append(
                f"{cite.inline_ref()} **{cite.title}** — {cite.url or 'n/a'}"
            )
            if cite.excerpt:
                lines.append(f"   > {cite.excerpt[:180]}")
        return "\n".join(lines)

    def attach_citations_to_text(self, text: str, max_refs: int = 6) -> str:
        """Append numeric citation markers to paragraphs lacking refs."""
        cites = list(self._citations.values())[:max_refs]
        if not cites:
            return text
        markers = " ".join(c.inline_ref() for c in cites[:3])
        if re.search(r"\[\d+\]", text):
            return text
        paras = [p for p in text.split("\n\n") if p.strip()]
        if paras and markers:
            paras[0] = f"{paras[0].rstrip()} {markers}"
        return "\n\n".join(paras)

    def all_citations(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._citations.values()]

    def all_claim_links(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._claim_links]

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "citations": self.all_citations(),
            "claimLinks": self.all_claim_links(),
        }
