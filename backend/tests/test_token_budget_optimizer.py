"""Tests for token budget optimizer."""

from app.execution.token_budget_optimizer import (
    build_role_context,
    estimate_tokens,
    resolve_role_budget,
)


def test_estimate_tokens():
    assert estimate_tokens("hello world test") >= 2
    assert estimate_tokens("") == 0


def test_lightweight_budget_smaller_than_reasoning():
    light = resolve_role_budget("lightweight")
    heavy = resolve_role_budget("reasoning")
    assert light.max_total_chars < heavy.max_total_chars


def test_synthesis_budget_condenses():
    outputs = {
        "n1": {
            "agent_name": "Researcher",
            "output": "A" * 2000,
        }
    }
    ctx, meta = build_role_context(
        initial_context="Objective: test",
        outputs=outputs,
        tool_calls=[],
        model_tier="standard",
        is_synthesis=True,
    )
    assert meta["isSynthesis"] is True
    assert meta["estimatedTokens"] < estimate_tokens("A" * 2000)
    assert len(ctx) < 4000


def test_retry_budget():
    role = resolve_role_budget("standard", is_retry=True)
    assert role.max_total_chars <= 3000
