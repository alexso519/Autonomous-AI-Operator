"""Tests for hallucination guard layer."""

from app.execution.hallucination_guard import HallucinationGuard


def test_research_without_tools_triggers_retry():
    assessment = HallucinationGuard.assess(
        "According to research, 87% of companies adopted AI in 2024 with 2 million users.",
        goal="Research current AI adoption trends",
        tool_calls=[],
        is_research=True,
    )
    assert assessment.should_tool_retry
    assert assessment.hallucination_risk > 0.3
    assert "research_without_tool_evidence" in assessment.indicators


def test_tool_grounded_lowers_risk():
    assessment = HallucinationGuard.assess(
        "Based on web search results from example.com, adoption is growing.",
        goal="Research AI trends",
        tool_calls=[{"status": "completed", "tool_name": "web_search"}],
        is_research=True,
    )
    assert assessment.tool_grounded
    assert assessment.hallucination_risk < 0.4


def test_label_unsupported_appends_section():
    text = "# Report\n\nSome content."
    labeled = HallucinationGuard.label_unsupported_in_text(
        text, ["Statistic: 87%", "Entity: Acme Corp"]
    )
    assert "Unsupported" in labeled
    assert "Statistic: 87%" in labeled


def test_factuality_penalty():
    assessment = HallucinationGuard.assess(
        "Studies indicate 90% growth.",
        goal="Research market growth",
        tool_calls=[],
        is_research=True,
    )
    adjusted = HallucinationGuard.apply_factuality_penalty(0.85, assessment)
    assert adjusted < 0.85
