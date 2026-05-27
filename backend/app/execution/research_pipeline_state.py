"""Shared helpers for pre-collected research pipeline state."""

from __future__ import annotations

from app.config.locale import is_zh_hk
from app.execution.context_manager import ContextManager
from app.execution.research_memory import ResearchMemory


def is_research_pipeline_ready(ctx: ContextManager) -> bool:
    """True when the web research pipeline has populated usable evidence."""
    if ctx.get_workflow_memory("research_pipeline_completed"):
        return True

    mem = ResearchMemory(ctx)._store()
    if mem.get("sources"):
        return True
    if mem.get("researchMode"):
        return True

    summary = ctx.get_workflow_memory("research_pipeline_summary") or {}
    if summary.get("sourceCount"):
        return True

    return bool(str(ctx.get_workflow_memory("research_context_block") or "").strip())


def research_pipeline_context_block(ctx: ContextManager) -> str:
    """Prompt block with pre-fetched research for synthesis-only agents."""
    if not is_research_pipeline_ready(ctx):
        return ""

    summary = ctx.get_workflow_memory("research_pipeline_summary") or {}
    block = str(ctx.get_workflow_memory("research_context_block") or "").strip()
    if not block:
        mem = ResearchMemory(ctx)._store()
        sources = mem.get("sources") or []
        if sources:
            header = "已收集來源：" if is_zh_hk() else "Collected sources:"
            lines = [header]
            for src in sources[:8]:
                evidence = src.get("evidenceScore", src.get("evidence_score", "n/a"))
                lines.append(
                    f"- [{src.get('title', 'Source')}] {src.get('url', '')} "
                    f"({'證據' if is_zh_hk() else 'evidence'} {evidence})"
                )
                snippet = src.get("snippet") or src.get("fetch_text") or ""
                if snippet:
                    lines.append(f"  {str(snippet)[:280]}")
            block = "\n".join(lines)

    if is_zh_hk():
        lines = [
            "--- 預先收集的研究證據 ---",
            "研究管道已完成網絡搜尋並擷取權威來源。",
            "請使用以下證據。除非核實單一爭議聲明，否則勿調用 web_search 或 webpage_fetch。",
        ]
        pipeline_summary = summary.get("summary")
        if pipeline_summary:
            lines.append(f"管道摘要：{pipeline_summary}")
        source_count = summary.get("sourceCount")
        if source_count:
            lines.append(f"已收集來源數：{source_count}")
    else:
        lines = [
            "--- Pre-collected Research Evidence ---",
            "A research pipeline has ALREADY searched the web and fetched authoritative sources.",
            "Use the evidence below. Do NOT call web_search or webpage_fetch unless verifying one disputed claim.",
        ]
        pipeline_summary = summary.get("summary")
        if pipeline_summary:
            lines.append(f"Pipeline summary: {pipeline_summary}")
        source_count = summary.get("sourceCount")
        if source_count:
            lines.append(f"Sources collected: {source_count}")
    if block:
        lines.extend(["", block[:4000]])
    return "\n".join(lines)


def pipeline_synthesis_mode_block() -> str:
    if is_zh_hk():
        return (
            "--- 綜合模式 ---\n"
            "以下提供預先收集的研究證據，請據此綜合你的答案。\n"
            "僅以 Markdown 正文回覆，勿調用工具或輸出 JSON。\n"
        )
    return (
        "--- Synthesis Mode ---\n"
        "Pre-collected research evidence is provided below. Synthesize it into your answer.\n"
        "Respond in markdown prose only. Do NOT call tools or emit JSON.\n"
    )
