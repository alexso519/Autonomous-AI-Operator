"""
Unified token and context budgeting.

Single budgeting system replacing fragmented logic across
token_budget_optimizer, context_compression, and context_manager.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.execution.context_compression import ContextBudget, build_compressed_context
from app.execution.memory_ranker import MemoryRecord, RankedMemory


@dataclass(frozen=True)
class RoleBudgetProfile:
    """Per-role context limits."""

    max_total_chars: int
    full_output_count: int
    max_full_output_chars: int
    max_compressed_output_chars: int
    max_tool_result_chars: int
    synthesis_reserve_chars: int = 0
    grounding_reserve_chars: int = 600


ROLE_PROFILES: dict[str, RoleBudgetProfile] = {
    "lightweight": RoleBudgetProfile(2400, 1, 800, 250, 500, 0, 400),
    "standard": RoleBudgetProfile(3800, 2, 1200, 350, 800, 0, 600),
    "reasoning": RoleBudgetProfile(5200, 2, 1600, 450, 1000, 0, 800),
    "synthesis": RoleBudgetProfile(3200, 1, 900, 200, 600, 400, 800),
    "retry": RoleBudgetProfile(2800, 1, 700, 250, 700, 0, 700),
}


@dataclass
class SectionBudget:
    """Budget allocation for a context section."""

    name: str
    max_chars: int
    preserve: bool = False


@dataclass
class BudgetAllocation:
    """Full budget breakdown for an assembly."""

    profile: RoleBudgetProfile
    sections: list[SectionBudget]
    total_cap: int
    estimated_tokens: int = 0
    truncated: list[str] | None = None


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)


class ContextBudgetManager:
    """Central token budgeting for context assembly."""

    @classmethod
    def resolve_profile(
        cls,
        model_tier: str = "standard",
        is_synthesis: bool = False,
        is_retry: bool = False,
    ) -> RoleBudgetProfile:
        if is_retry:
            return ROLE_PROFILES["retry"]
        if is_synthesis:
            return ROLE_PROFILES["synthesis"]
        tier = model_tier.lower()
        if tier in ("lightweight", "light"):
            return ROLE_PROFILES["lightweight"]
        if tier in ("reasoning", "heavy"):
            return ROLE_PROFILES["reasoning"]
        return ROLE_PROFILES.get(tier, ROLE_PROFILES["standard"])

    @classmethod
    def to_context_budget(cls, profile: RoleBudgetProfile) -> ContextBudget:
        return ContextBudget(
            full_output_count=profile.full_output_count,
            max_full_output_chars=profile.max_full_output_chars,
            max_compressed_output_chars=profile.max_compressed_output_chars,
            max_total_context_chars=profile.max_total_chars,
            max_tool_result_chars=profile.max_tool_result_chars,
        )

    @classmethod
    def allocate_sections(
        cls,
        profile: RoleBudgetProfile,
        ranked: list[RankedMemory],
    ) -> BudgetAllocation:
        """Allocate char budgets per section type."""
        total = profile.max_total_chars
        grounding = profile.grounding_reserve_chars
        synthesis = profile.synthesis_reserve_chars if profile.synthesis_reserve_chars else 0

        sections = [
            SectionBudget("initial_context", min(800, total // 5), preserve=True),
            SectionBudget("tool_results", profile.max_tool_result_chars, preserve=True),
            SectionBudget("evidence", grounding, preserve=True),
            SectionBudget("research", min(600, total // 4)),
            SectionBudget("memory_tiers", min(500, total // 5)),
            SectionBudget("reasoning", min(400, total // 6)),
            SectionBudget("shared_memory", min(300, total // 8)),
            SectionBudget("agent_outputs", total - grounding - synthesis - 800),
        ]
        if synthesis:
            sections.append(SectionBudget("synthesis_findings", synthesis, preserve=True))

        return BudgetAllocation(profile=profile, sections=sections, total_cap=total)

    @classmethod
    def build_workflow_context(
        cls,
        initial_context: str,
        outputs: dict[str, dict[str, Any]],
        tool_calls: list[dict[str, Any]] | None,
        profile: RoleBudgetProfile,
        *,
        is_synthesis: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Build core workflow context block (backward-compatible)."""
        condensed_outputs = outputs
        if is_synthesis:
            condensed_outputs = {}
            for nid, entry in outputs.items():
                out = entry.get("output", "")
                if len(out) > 400:
                    out = out[:200] + " … " + out[-150:]
                condensed_outputs[nid] = {**entry, "output": out}

        budget = cls.to_context_budget(profile)
        context = build_compressed_context(
            initial_context=initial_context,
            outputs=condensed_outputs,
            tool_calls=tool_calls,
            budget=budget,
        )
        meta = {
            "maxTotalChars": profile.max_total_chars,
            "estimatedTokens": estimate_tokens(context),
            "fullOutputCount": profile.full_output_count,
            "isSynthesis": is_synthesis,
        }
        return context, meta

    @classmethod
    def truncate_section(cls, text: str, max_chars: int, label: str = "") -> tuple[str, bool]:
        """Deterministically truncate a section."""
        if len(text) <= max_chars:
            return text, False
        suffix = f"\n... [{label or 'truncated'}]"
        return text[: max_chars - len(suffix)] + suffix, True

    @classmethod
    def assemble_sections(
        cls,
        sections: list[tuple[str, str, bool]],
        total_cap: int,
    ) -> tuple[str, list[str], int]:
        """
        Merge ordered sections with hard cap.

        Args:
            sections: list of (name, content, preserve) tuples.
            total_cap: hard character cap.

        Returns:
            (combined_text, truncated_section_names, chars_removed)
        """
        parts: list[str] = []
        truncated: list[str] = []
        preserve_chars = sum(len(c) for _, c, p in sections if p and c.strip())
        remaining = max(total_cap - preserve_chars, total_cap // 3)

        for name, content, preserve in sections:
            if not content.strip():
                continue
            if preserve:
                parts.append(content)
                continue
            if len(content) <= remaining:
                parts.append(content)
                remaining -= len(content) + 2
            else:
                truncated_text, was_trunc = cls.truncate_section(content, remaining, name)
                parts.append(truncated_text)
                if was_trunc:
                    truncated.append(name)
                remaining = 0

        combined = "\n\n".join(parts)
        chars_removed = 0
        if len(combined) > total_cap:
            chars_removed = len(combined) - total_cap
            combined = combined[: total_cap - 20] + "\n... [context truncated]"
            truncated.append("_total_")

        return combined, truncated, chars_removed

    @classmethod
    def format_memory_sections(
        cls,
        ranked: list[RankedMemory],
        allocation: BudgetAllocation,
    ) -> list[tuple[str, str, bool]]:
        """Convert ranked memories into budgeted sections."""
        by_section: dict[str, list[str]] = {}
        budget_map = {s.name: s for s in allocation.sections}

        for rm in ranked:
            rec = rm.record
            section = rec.section or rec.namespace
            by_section.setdefault(section, []).append(rec.content)

        result: list[tuple[str, str, bool]] = []
        for section_name, lines in sorted(by_section.items()):
            budget = budget_map.get(section_name)
            max_chars = budget.max_chars if budget else allocation.total_cap // 4
            preserve = budget.preserve if budget else False
            content = "\n".join(lines)
            content, _ = cls.truncate_section(content, max_chars, section_name)
            result.append((section_name, content, preserve))
        return result

    @classmethod
    def utilization(cls, used_chars: int, total_cap: int) -> float:
        if total_cap <= 0:
            return 0.0
        return min(1.0, used_chars / total_cap)
