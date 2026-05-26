"""Tests for unified context budget manager."""

from app.execution.context_budget_manager import ContextBudgetManager, estimate_tokens


def test_lightweight_budget_smaller_than_reasoning():
    light = ContextBudgetManager.resolve_profile("lightweight")
    heavy = ContextBudgetManager.resolve_profile("reasoning")
    assert light.max_total_chars < heavy.max_total_chars


def test_synthesis_profile_has_reserve():
    synth = ContextBudgetManager.resolve_profile(is_synthesis=True)
    assert synth.synthesis_reserve_chars > 0


def test_build_workflow_context_respects_cap():
    outputs = {
        "n1": {"agent_name": "Agent", "output": "X" * 5000},
    }
    profile = ContextBudgetManager.resolve_profile("lightweight")
    context, meta = ContextBudgetManager.build_workflow_context(
        initial_context="Objective",
        outputs=outputs,
        tool_calls=[],
        profile=profile,
    )
    assert len(context) <= profile.max_total_chars + 30
    assert meta["estimatedTokens"] == estimate_tokens(context)


def test_assemble_sections_truncates_non_preserved():
    sections = [
        ("evidence", "E" * 500, True),
        ("agent_outputs", "A" * 3000, False),
    ]
    combined, truncated, _removed = ContextBudgetManager.assemble_sections(
        sections, total_cap=2000
    )
    assert len(combined) <= 2000 + 30
    assert "evidence" in combined or truncated


def test_utilization_calculation():
    assert ContextBudgetManager.utilization(850, 1000) == 0.85
    assert ContextBudgetManager.utilization(1200, 1000) == 1.0


def test_retry_profile_tighter_than_standard():
    retry = ContextBudgetManager.resolve_profile(is_retry=True)
    standard = ContextBudgetManager.resolve_profile("standard")
    assert retry.max_total_chars <= standard.max_total_chars
