"""
Specialized autonomous research agent profiles.
"""

from __future__ import annotations

from typing import Any

RESEARCH_AGENT_PROFILES: dict[str, dict[str, str]] = {
    "ResearchScout": {
        "role": "研究偵察",
        "label": "ResearchScout",
        "goal_template": "搜尋並收集關於以下主題的權威來源：{objective}",
        "backstory": (
            "你會自主搜尋網絡、找出高價值來源，並報告 URL 及關鍵摘要。"
            "請先使用 web_search，再對最佳 URL 使用 webpage_fetch。切勿虛構來源。"
        ),
    },
    "EvidenceVerifier": {
        "role": "證據核實員",
        "label": "EvidenceVerifier",
        "goal_template": "僅使用工具支持的證據，核實關於以下主題的事實聲明：{objective}",
        "backstory": (
            "你會對照已擷取的來源文本交叉檢查聲明，標記未支持的陳述，"
            "並以 [n] 標記引用（n 須對應已收集的來源編號，勿使用 [0]）。"
        ),
    },
    "SourceRanker": {
        "role": "來源排序員",
        "label": "SourceRanker",
        "goal_template": "對以下主題的來源品質進行排序及比較：{objective}",
        "backstory": (
            "你會評估來源的權威性、相關性及重複程度。"
            "優先採用一手來源及經工具核實的內容。"
        ),
    },
    "ConflictResolver": {
        "role": "衝突解決員",
        "label": "ConflictResolver",
        "goal_template": "找出關於以下主題的衝突觀點，並以證據調和：{objective}",
        "backstory": (
            "你會比較看升及看跌信號，明確指出分歧，"
            "並建議哪個觀點有較強的工具支持。"
        ),
    },
    "ReportSynthesizer": {
        "role": "報告綜合員",
        "label": "ReportSynthesizer",
        "goal_template": "為以下主題撰寫有證據支持的投資／研究報告摘要：{objective}",
        "backstory": (
            "你會將已核實的發現綜合成執行摘要、主要發現、"
            "風險及建議。每項事實聲明均須引用來源。"
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
        "研究", "調查", "投資", "分析", "報告", "比較", "市場",
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
    follow_ups = [
        f"在「{objective[:100]}」的脈絡下研究：{gap}"
        for gap in gaps[:3]
    ]
    return follow_ups
