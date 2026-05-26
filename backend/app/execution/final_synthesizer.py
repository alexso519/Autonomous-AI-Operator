"""
Final response synthesis — produces a polished markdown report from execution outputs.

Runs after workflow completion without modifying the execution engine loop.
Uses deterministic rule-based assembly (no extra LLM call) for stability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.execution.context_manager import ContextManager
from app.execution.hallucination_guard import HallucinationGuard


@dataclass
class EvidenceItem:
    source: str
    text: str
    quality: float
    tool_backed: bool


@dataclass
class SynthesizedResponse:
    markdown: str
    executive_summary: str
    actionable_answer: str
    key_findings: list[str]
    risks: list[str]
    recommendations: list[str]
    sections_used: list[str]
    retry_count: int
    tool_count: int
    quality_score: float | None = None
    conflicts: list[dict[str, str]] | None = None
    evidence_ranked: list[dict[str, Any]] | None = None
    provenance: list[str] | None = None
    confidence_summary: str | None = None


def _is_retry_step(step: dict[str, Any]) -> bool:
    name = (step.get("agentName") or "").lower()
    return name.startswith("retry ") or "retry (" in name or "retry" in name


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _dedupe_paragraphs(texts: list[str]) -> list[str]:
    """Aggressively remove near-duplicate paragraphs across agent outputs."""
    seen: set[str] = set()
    unique: list[str] = []
    for text in texts:
        for para in re.split(r"\n\n+", text.strip()):
            para = para.strip()
            if len(para) < 30:
                continue
            key = _normalize_text(para)[:160]
            if key in seen:
                continue
            # Also skip if paragraph is substring of an existing one
            if any(key in s or s in key for s in seen if len(s) > 40):
                continue
            seen.add(key)
            unique.append(para)
    return unique


def _merge_overlapping_sections(texts: list[str]) -> list[str]:
    """Merge sections with identical headings."""
    sections: dict[str, list[str]] = {}
    for text in texts:
        current_heading = "body"
        for line in text.split("\n"):
            heading_match = re.match(r"^#{1,3}\s+(.+)$", line.strip())
            if heading_match:
                current_heading = heading_match.group(1).lower()
                sections.setdefault(current_heading, [])
            elif line.strip():
                sections.setdefault(current_heading, []).append(line.strip())

    merged: list[str] = []
    for heading, lines in sections.items():
        deduped_lines: list[str] = []
        seen_lines: set[str] = set()
        for line in lines:
            key = _normalize_text(line)[:100]
            if key not in seen_lines:
                seen_lines.add(key)
                deduped_lines.append(line)
        if heading != "body":
            merged.append(f"### {heading.title()}")
        merged.extend(deduped_lines)
        merged.append("")
    return merged


def _extract_bullets(text: str, limit: int = 8) -> list[str]:
    bullets = re.findall(r"^[-*]\s+(.+)$", text, re.MULTILINE)
    seen: set[str] = set()
    result: list[str] = []
    for b in bullets:
        key = _normalize_text(b)[:80]
        if key not in seen:
            seen.add(key)
            result.append(b.strip())
        if len(result) >= limit:
            break
    return result


def _extract_risks(text: str) -> list[str]:
    risk_patterns = [
        r"(?i)(?:risk|concern|caveat|limitation|uncertain)[:\s]+([^.!\n]+[.!])",
        r"(?i)(?:may not|could fail|might cause)([^.!\n]+[.!])",
    ]
    risks: list[str] = []
    for pattern in risk_patterns:
        for match in re.finditer(pattern, text):
            risks.append(match.group(0).strip()[:200])
    return list(dict.fromkeys(risks))[:5]


def _extract_findings(text: str) -> list[str]:
    findings = _extract_bullets(text, 6)
    if not findings:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        findings = [s.strip() for s in sentences if len(s.strip()) > 40][:4]
    return findings


def _first_sentences(text: str, count: int = 2) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(s for s in sentences[:count] if s).strip()


def _extract_headings(text: str) -> list[str]:
    return re.findall(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)


def _detect_conflicts(outputs: list[tuple[str, str]]) -> list[dict[str, str]]:
    """Find contradictory numeric or directional claims across agents."""
    conflicts: list[dict[str, str]] = []
    growth_up = re.compile(r"\b(grow|growth|increase|bullish|outperform)\b", re.I)
    growth_down = re.compile(r"\b(decline|decrease|bearish|underperform|risk)\b", re.I)

    for i, (name_a, text_a) in enumerate(outputs):
        for name_b, text_b in outputs[i + 1:]:
            if growth_up.search(text_a) and growth_down.search(text_b):
                conflicts.append(
                    {
                        "agents": f"{name_a} vs {name_b}",
                        "topic": "directional outlook",
                        "resolution": "Prefer tool-backed evidence; see provenance.",
                    }
                )
            elif growth_down.search(text_a) and growth_up.search(text_b):
                conflicts.append(
                    {
                        "agents": f"{name_a} vs {name_b}",
                        "topic": "directional outlook",
                        "resolution": "Prefer tool-backed evidence; see provenance.",
                    }
                )
    return conflicts[:5]


def _rank_evidence(
    steps: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    confidence_history: list[dict[str, Any]],
) -> list[EvidenceItem]:
    conf_by_node = {
        c.get("nodeId"): c.get("score", 0.5)
        for c in confidence_history
        if c.get("nodeId")
    }
    tool_nodes = {
        t.get("node_id") for t in tool_calls if t.get("status") == "completed"
    }
    ranked: list[EvidenceItem] = []
    for step in steps:
        if step.get("status") != "completed":
            continue
        node_id = step.get("nodeId", "")
        name = step.get("agentName", "Agent")
        text = (step.get("output") or "").strip()
        if len(text) < 40:
            continue
        tool_backed = node_id in tool_nodes
        quality = conf_by_node.get(node_id, 0.55)
        if tool_backed:
            quality = min(1.0, quality + 0.15)
        ranked.append(
            EvidenceItem(
                source=name,
                text=text[:400],
                quality=quality,
                tool_backed=tool_backed,
            )
        )
    ranked.sort(key=lambda e: e.quality, reverse=True)
    return ranked


class FinalResponseSynthesizer:
    """Assemble a clean final answer from completed execution artifacts."""

    @classmethod
    def synthesize(
        cls,
        workflow_name: str,
        objective: str,
        steps: list[dict[str, Any]],
        ctx: ContextManager,
    ) -> SynthesizedResponse:
        tool_calls = ctx.get_workflow_memory("tool_calls") or []
        reflection_history = ctx.get_workflow_memory("reflection_history") or []
        replanning_history = ctx.get_workflow_memory("replanning_history") or []
        confidence_history = ctx.get_workflow_memory("confidence_history") or []
        last_quality = ctx.get_workflow_memory("last_quality_score") or {}
        quality_score = last_quality.get("overallScore")
        selected_strategy = ctx.get_workflow_memory("selected_strategy") or {}
        citation_snap = ctx.get_workflow_memory("citations") or {}
        research_citations = citation_snap.get("citations") or []
        provenance_records = ctx.get_workflow_memory("provenance_records") or []
        evidence_graph_snap = ctx.get_workflow_memory("evidence_graph") or {}
        pipeline_summary = ctx.get_workflow_memory("research_pipeline_summary") or {}

        primary_steps = [
            s for s in steps
            if s.get("status") == "completed" and not _is_retry_step(s)
        ]
        if not primary_steps:
            primary_steps = [s for s in steps if s.get("status") == "completed"]

        outputs = [s.get("output", "").strip() for s in primary_steps if s.get("output")]
        agent_output_pairs = [
            (s.get("agentName", "Agent"), s.get("output", "").strip())
            for s in primary_steps
            if s.get("output")
        ]
        conflicts = _detect_conflicts(agent_output_pairs)
        evidence_ranked = _rank_evidence(
            primary_steps,
            tool_calls if isinstance(tool_calls, list) else [],
            confidence_history if isinstance(confidence_history, list) else [],
        )
        provenance = [
            f"{e.source} (confidence {e.quality:.0%}"
            f"{', tool-backed' if e.tool_backed else ''})"
            for e in evidence_ranked[:8]
        ]
        conf_scores = [
            c.get("score") for c in confidence_history
            if isinstance(c, dict) and c.get("score") is not None
        ]
        avg_conf = sum(conf_scores) / len(conf_scores) if conf_scores else None
        confidence_summary = (
            f"Average agent confidence: {avg_conf:.0%}"
            if avg_conf is not None
            else None
        )

        deduped = _dedupe_paragraphs(outputs)
        merged = _merge_overlapping_sections(outputs)
        body_source = "\n\n".join(deduped) if deduped else "\n".join(merged)

        writer_output = ""
        for step in reversed(primary_steps):
            name = (step.get("agentName") or "").lower()
            if any(k in name for k in ("writer", "analyst", "research", "summary")):
                writer_output = step.get("output", "").strip()
                break
        main_body = writer_output or (outputs[-1] if outputs else "(No agent output captured.)")

        executive = _first_sentences(main_body, 2)
        if not executive and deduped:
            executive = _first_sentences(deduped[0], 2)
        if not executive:
            executive = f"Completed autonomous execution for: {objective[:180]}"

        key_findings = _extract_findings(main_body)
        if not key_findings and deduped:
            key_findings = _extract_findings("\n".join(deduped[:3]))

        risks = _extract_risks(main_body)
        recommendations = _extract_bullets(main_body, 5)
        if not recommendations:
            rec_match = re.search(
                r"(?i)(?:recommend(?:ation)?s?|next steps?|action items?)[:\s]*\n((?:[-*].+\n?)+)",
                main_body,
            )
            if rec_match:
                recommendations = _extract_bullets(rec_match.group(0), 5)

        actionable = ""
        if recommendations:
            actionable = "\n".join(f"- {r}" for r in recommendations[:5])
        else:
            paras = [p.strip() for p in main_body.split("\n\n") if p.strip()]
            actionable = paras[-1][:400] if paras else executive[:400]

        retry_count = sum(1 for s in steps if _is_retry_step(s))
        completed_tools = [t for t in tool_calls if t.get("status") == "completed"]

        sections_used = []
        for out in outputs:
            sections_used.extend(_extract_headings(out))

        lines = [
            f"# Final Report: {workflow_name}",
            "",
            f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
            "",
            "## Executive Summary",
            "",
            executive,
            "",
        ]

        if key_findings:
            lines.extend(["## Key Findings", ""])
            for finding in key_findings[:6]:
                lines.append(f"- {finding}")
            lines.append("")

        if risks:
            lines.extend(["## Risks & Limitations", ""])
            for risk in risks:
                lines.append(f"- {risk}")
            lines.append("")

        lines.extend(["## Recommendations", ""])
        if recommendations:
            for rec in recommendations[:6]:
                lines.append(f"- {rec}")
        else:
            lines.append(actionable or "See detailed analysis below.")
        lines.append("")

        if research_citations:
            lines.extend(["## Citations", ""])
            for cite in research_citations[:10]:
                marker = cite.get("marker", "?")
                lines.append(
                    f"[{marker}] **{cite.get('title', 'Source')}** — "
                    f"{cite.get('url', 'n/a')}"
                )
                ex = cite.get("excerpt", "")
                if ex:
                    lines.append(f"   > {ex[:200]}")
            lines.append("")

        if pipeline_summary.get("summary"):
            lines.extend(["## Autonomous Research Summary", ""])
            lines.append(pipeline_summary["summary"][:2500])
            comp = pipeline_summary.get("comparison") or {}
            if comp.get("conflictLikely"):
                lines.append(
                    f"\n*Note: {comp.get('summary', 'Conflicting viewpoints detected')}*"
                )
            lines.append("")

        if completed_tools:
            lines.extend(["## Research & Tool Evidence", ""])
            for call in completed_tools[:8]:
                name = call.get("tool_name", "tool")
                if name == "web_search":
                    for hit in (call.get("output") or {}).get("results", [])[:3]:
                        lines.append(
                            f"- **{hit.get('title', 'Result')}**: "
                            f"{hit.get('snippet', '')[:220]}"
                        )
                elif name == "webpage_fetch":
                    out = call.get("output") or {}
                    lines.append(
                        f"- **Source**: {out.get('url', '')} — "
                        f"{str(out.get('text', ''))[:180]}"
                    )
                else:
                    lines.append(f"- `{name}`: {str(call.get('output', ''))[:160]}")
            lines.append("")

        lines.extend(["## Detailed Analysis", ""])
        body_text = body_source
        if len(body_text) > 5000:
            body_text = body_text[:5000] + "\n\n... [truncated for display]"
        lines.append(body_text)
        lines.append("")

        if conflicts:
            lines.extend(["## Conflicting Perspectives", ""])
            for c in conflicts:
                lines.append(
                    f"- **{c.get('agents', '?')}** ({c.get('topic', 'topic')}): "
                    f"{c.get('resolution', 'Review evidence below.')}"
                )
            lines.append("")

        if provenance_records:
            for rec in provenance_records[:8]:
                if isinstance(rec, dict):
                    provenance.append(
                        f"{rec.get('toolName', 'tool')}: {rec.get('fact', '')[:80]} "
                        f"({rec.get('confidence', 0):.0%}) "
                        f"[{rec.get('citationMarker', '')}]"
                    )

        if provenance:
            lines.extend(["## Evidence Provenance", ""])
            for p in provenance:
                lines.append(f"- {p}")
            if evidence_graph_snap.get("conflictCount"):
                lines.append(
                    f"- Evidence graph: {evidence_graph_snap.get('evidenceCount', 0)} "
                    f"items, {evidence_graph_snap.get('conflictCount', 0)} conflict(s)"
                )
            if selected_strategy.get("label"):
                lines.append(
                    f"- Execution strategy: **{selected_strategy['label']}** "
                    f"(score {selected_strategy.get('compositeScore', 0):.0%})"
                )
            if confidence_summary:
                lines.append(f"- {confidence_summary}")
            lines.append("")

        if retry_count or reflection_history or replanning_history or quality_score:
            lines.extend(["## Execution Notes", ""])
            if quality_score is not None:
                lines.append(f"- Final quality score: {quality_score:.0%}")
            if retry_count:
                lines.append(f"- {retry_count} typed retry step(s) executed.")
            if reflection_history:
                lines.append(f"- {len(reflection_history)} quality reflection check(s).")
            if replanning_history:
                lines.append(f"- {len(replanning_history)} replanning action(s).")
            lines.append("")

        markdown = "\n".join(lines).strip()

        # Label unsupported claims from hallucination guard
        all_output_text = "\n".join(outputs)
        guard = HallucinationGuard.assess(
            all_output_text,
            goal=objective,
            tool_calls=tool_calls,
            is_research=HallucinationGuard.RESEARCH_GOAL.search(objective) is not None,
        )
        if guard.unsupported_claims:
            markdown = HallucinationGuard.label_unsupported_in_text(
                markdown, guard.unsupported_claims
            )

        return SynthesizedResponse(
            markdown=markdown,
            executive_summary=executive,
            actionable_answer=actionable,
            key_findings=key_findings,
            risks=risks,
            recommendations=recommendations,
            sections_used=sections_used[:20],
            retry_count=retry_count,
            tool_count=len(completed_tools),
            quality_score=quality_score,
            conflicts=conflicts,
            evidence_ranked=[
                {
                    "source": e.source,
                    "quality": round(e.quality, 3),
                    "toolBacked": e.tool_backed,
                    "excerpt": e.text[:200],
                }
                for e in evidence_ranked[:10]
            ],
            provenance=provenance,
            confidence_summary=confidence_summary,
        )
