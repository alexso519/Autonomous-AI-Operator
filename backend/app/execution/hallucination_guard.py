"""
Hallucination reduction layer — grounded output verification.

Detects unsupported claims, missing citations, and absent tool evidence.
Feeds reflection/retry policy and final synthesizer labeling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HallucinationAssessment:
    unsupported_claims: list[str] = field(default_factory=list)
    missing_citations: bool = False
    tool_grounded: bool = False
    confidence_penalty: float = 0.0
    factuality_downgrade: float = 0.0
    hallucination_risk: float = 0.0
    indicators: list[str] = field(default_factory=list)
    should_tool_retry: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "unsupportedClaims": self.unsupported_claims[:10],
            "missingCitations": self.missing_citations,
            "toolGrounded": self.tool_grounded,
            "confidencePenalty": round(self.confidence_penalty, 3),
            "factualityDowngrade": round(self.factuality_downgrade, 3),
            "hallucinationRisk": round(self.hallucination_risk, 3),
            "indicators": self.indicators,
            "shouldToolRetry": self.should_tool_retry,
        }


class HallucinationGuard:
    """Deterministic factuality checks for agent outputs."""

    STAT_CLAIM = re.compile(
        r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?%|\d+(?:\.\d+)?\s*(?:million|billion|thousand|users|downloads|percent))\b",
        re.IGNORECASE,
    )
    DATE_CLAIM = re.compile(
        r"\b(?:in|since|by|on)\s+(?:19|20)\d{2}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    )
    CITATION_PATTERN = re.compile(
        r"\b(?:source|according to|cite[sd]?|reference|via|from)\b|https?://|\[[^\]]+\]\(http|\[\d{1,2}\]",
        re.IGNORECASE,
    )
    UNSUPPORTED_PHRASE = re.compile(
        r"\b(research shows|studies indicate|data suggests|experts agree|it is known that|reportedly|widely believed)\b",
        re.IGNORECASE,
    )
    RESEARCH_GOAL = re.compile(
        r"\b(research|investigate|find|lookup|search|current|latest|news|verify|fact)\b",
        re.IGNORECASE,
    )
    SPECIFIC_ENTITY = re.compile(
        r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\s+(?:Inc|Corp|LLC|Ltd|Foundation|Institute|University)\b"
    )

    @classmethod
    def assess(
        cls,
        output: str,
        goal: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        is_research: bool | None = None,
        pipeline_grounded: bool = False,
    ) -> HallucinationAssessment:
        text = (output or "").strip()
        goal_lower = (goal or "").lower()
        research = is_research if is_research is not None else bool(cls.RESEARCH_GOAL.search(goal_lower))
        tools = tool_calls or []
        completed = [t for t in tools if t.get("status") == "completed"]
        node_tools = completed  # caller may pre-filter by node

        assessment = HallucinationAssessment()
        indicators: list[str] = []
        risk = 0.05
        penalty = 0.0
        downgrade = 0.0
        unsupported: list[str] = []

        if not text or text == "(no output)":
            assessment.hallucination_risk = 0.0
            assessment.indicators = ["empty_output"]
            return assessment

        # Tool grounding
        assessment.tool_grounded = len(completed) > 0 or pipeline_grounded
        if research and not completed and not pipeline_grounded:
            risk += 0.35
            downgrade += 0.3
            penalty += 0.25
            indicators.append("research_without_tool_evidence")
            assessment.should_tool_retry = True
        elif pipeline_grounded:
            indicators.append("pipeline_evidence_grounded")

        # Citation presence for factual claims
        has_citation = bool(cls.CITATION_PATTERN.search(text))
        has_unsupported_phrase = bool(cls.UNSUPPORTED_PHRASE.search(text))
        if has_unsupported_phrase and not has_citation and not pipeline_grounded:
            risk += 0.25
            penalty += 0.2
            indicators.append("unsupported_authority_phrasing")
            unsupported.append("Authority claim without citation")

        assessment.missing_citations = has_unsupported_phrase and not has_citation

        # Statistical / date claims without grounding
        stat_matches = cls.STAT_CLAIM.findall(text)
        date_matches = cls.DATE_CLAIM.findall(text)
        if (stat_matches or date_matches) and not has_citation and not completed and not pipeline_grounded:
            risk += 0.2
            downgrade += 0.15
            indicators.append("specific_stats_without_evidence")
            for m in stat_matches[:3]:
                unsupported.append(f"Statistic: {m}")
            for m in date_matches[:2]:
                unsupported.append(f"Date claim: {m}")

        # Named entities without tool evidence
        entities = cls.SPECIFIC_ENTITY.findall(text)
        if entities and research and not completed and not pipeline_grounded:
            risk += 0.15
            indicators.append("named_entities_unverified")
            unsupported.extend(f"Entity: {e}" for e in entities[:3])

        # Fake URLs
        if re.search(r"https?://[^\s]*(?:example|fake|placeholder|invalid)", text, re.I):
            risk += 0.4
            indicators.append("suspicious_url")
            unsupported.append("Suspicious or placeholder URL")

        if research and len(text.split()) > 80 and not completed and not pipeline_grounded:
            risk += 0.15
            indicators.append("long_answer_no_tools")
            assessment.should_tool_retry = True

        assessment.unsupported_claims = unsupported
        assessment.confidence_penalty = min(0.6, penalty)
        assessment.factuality_downgrade = min(0.5, downgrade)
        assessment.hallucination_risk = max(0.0, min(1.0, risk))
        assessment.indicators = indicators
        return assessment

    @classmethod
    def apply_factuality_penalty(cls, base_factuality: float, assessment: HallucinationAssessment) -> float:
        adjusted = base_factuality - assessment.factuality_downgrade - assessment.confidence_penalty
        return max(0.0, min(1.0, adjusted))

    @classmethod
    def label_unsupported_in_text(cls, text: str, unsupported: list[str]) -> str:
        """Append unsupported claims section for final synthesizer."""
        if not unsupported:
            return text
        lines = ["", "## ⚠ Unsupported or Unverified Claims", ""]
        for claim in unsupported[:8]:
            lines.append(f"- *Unverified*: {claim}")
        lines.append("")
        lines.append(
            "*These claims were not grounded in tool evidence or citations during execution.*"
        )
        return text + "\n" + "\n".join(lines)
