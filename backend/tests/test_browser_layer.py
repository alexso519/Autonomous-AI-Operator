"""Tests for browser automation layer."""

import sys
from types import ModuleType

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.browser.action_planner import ActionPlanner
from app.browser.ui_grounding import UIGrounding
from app.browser.page_memory import PageMemory, PageSnapshot, ActionRecord
from app.browser.browser_orchestrator import BrowserOrchestrator


def test_action_planner_nvidia_news():
    plan = ActionPlanner.plan(
        "Open GitHub, research latest NVIDIA Blackwell news, and summarize findings"
    )
    assert plan.plan_id
    assert len(plan.steps) >= 2
    assert "blackwell" in plan.search_query.lower() or "nvidia" in plan.start_url.lower()


def test_ui_grounding_extracts_html():
    html = """
    <html><head><title>Test Page</title></head>
    <body><h1>Hello</h1><p>This is a test paragraph with enough content.</p>
    <a href="https://example.com">Link</a></body></html>
    """
    content = UIGrounding.extract_from_html(html, "https://example.com")
    assert content.title == "Test Page"
    assert "Hello" in content.headings
    assert len(content.paragraphs) >= 1
    assert len(content.links) >= 1


def test_page_memory_replay_timeline():
    memory = PageMemory("test-session")
    memory.record_snapshot(PageSnapshot(
        snapshot_id="s1",
        url="https://example.com",
        title="Example",
        content_excerpt="content",
        screenshot_path=None,
    ))
    memory.record_action(ActionRecord(
        action_id="a1",
        action_type="navigate",
        target="https://example.com",
        value="",
        success=True,
    ))
    timeline = memory.get_replay_timeline()
    assert len(timeline) == 2


def test_browser_objective_detection():
    assert BrowserOrchestrator.is_browser_objective(
        "Navigate to GitHub and search for news"
    )
    assert not BrowserOrchestrator.is_browser_objective("Fix failing tests")
