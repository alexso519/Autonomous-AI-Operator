"""
Specialized autonomous research agent profiles.
"""

from __future__ import annotations

from typing import Any

RESEARCH_AGENT_PROFILES: dict[str, dict[str, str]] = {
    "ResearchScout": {
        "role": "Research Scout",
        "label": "ResearchScout",
        "goal_template": "Discover and collect authoritative sources about: {objective}",
        "backstory": (
            "You autonomously search the web, identify high-value sources, and "
            "report URLs and key snippets. Always use web_search first, then "
            "webpage_fetch on the best URLs. Never invent sources."
        ),
    },
    "EvidenceVerifier": {
        "role": "Evidence Verifier",
        "label": "EvidenceVerifier",
        "goal_template": "Verify factual claims about: {objective} using tool-backed evidence only.",
        "backstory": (
            "You cross-check claims against fetched source text. Flag unsupported "
            "statements and cite evidence with [n] markers."
        ),
    },
    "SourceRanker": {
        "role": "Source Ranker",
        "label": "SourceRanker",
        "goal_template": "Rank and compare source quality for: {objective}",
        "backstory": (
            "You evaluate source authority, relevance, and duplication. Prefer "
            "primary sources and tool-verified content."
        ),
    },
    "ConflictResolver": {
        "role": "Conflict Resolver",
        "label": "ConflictResolver",
        "goal_template": "Identify conflicting viewpoints on: {objective} and reconcile with evidence.",
        "backstory": (
            "You compare bullish vs bearish signals, note disagreements explicitly, "
            "and recommend which view has stronger tool-backed support."
        ),
    },
    "ReportSynthesizer": {
        "role": "Report Synthesizer",
        "label": "ReportSynthesizer",
        "goal_template": "Produce an evidence-backed investment/report summary for: {objective}",
        "backstory": (
            "You synthesize verified findings into executive summary, key findings, "
            "risks, and recommendations. Every factual claim must cite sources."
        ),
    },
}

RESEARCH_ROLE_ORDER = [
    "ResearchScout",
    "EvidenceVerifier",
    "SourceRanker",
    "ConflictResolver",
    "ReportSynthesizer",
]


def is_research_objective(objective: str) -> bool:
    lower = objective.lower()
    keywords = (
        "research", "investigate", "investment", "outlook", "analyze",
        "compare", "market", "report", "nvidia", "blackwell",
    )
    return any(k in lower for k in keywords)


def apply_research_profiles(
    nodes: list[dict[str, Any]],
    objective: str,
) -> list[dict[str, Any]]:
    """Map workflow agent nodes to specialized research roles when applicable."""
    if not is_research_objective(objective):
        return nodes

    agent_nodes = [
        n for n in nodes
        if n.get("type") != "approval"
        and n.get("data", {}).get("nodeType") != "approval"
    ]
    if not agent_nodes:
        return nodes

    updated = []
    for i, node in enumerate(nodes):
        if node.get("type") == "approval" or node.get("data", {}).get("nodeType") == "approval":
            updated.append(node)
            continue

        agent_idx = sum(
            1 for u in updated
            if u.get("type") != "approval"
            and u.get("data", {}).get("nodeType") != "approval"
        )
        if agent_idx < len(RESEARCH_ROLE_ORDER):
            profile_key = RESEARCH_ROLE_ORDER[agent_idx]
            profile = RESEARCH_AGENT_PROFILES[profile_key]
            data = dict(node.get("data") or {})
            data["label"] = profile["label"]
            data["role"] = profile["role"]
            data["goal"] = profile["goal_template"].format(objective=objective[:300])
            data["backstory"] = profile["backstory"]
            data["executionMetadata"] = {
                **(data.get("executionMetadata") or {}),
                "researchAgent": profile_key,
                "createdBy": "ResearchAgents",
            }
            node = {**node, "data": data}
        updated.append(node)

    return updated


async def generate_follow_up_research(
    objective: str,
    gaps: list[str],
    *,
    execution_id: str = "",
) -> list[str]:
    """Autonomous follow-up queries from detected knowledge gaps."""
    if not gaps:
        return []
    follow_ups = [f"Research {gap} in context of: {objective[:100]}" for gap in gaps[:3]]
    return follow_ups
