"""Tests for autonomous coding layer."""

import asyncio
import sys
from pathlib import Path
from types import ModuleType

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.coding.repo_analyzer import RepoAnalyzer
from app.coding.code_planner import CodePlanner
from app.coding.patch_executor import PatchExecutor
from app.coding.test_runner import TestRunner
from app.coding.debug_loop import DebugLoop
from app.coding.coding_orchestrator import CodingOrchestrator
from app.execution.coding_agents import (
    is_coding_objective,
    is_browser_objective,
    apply_coding_profiles,
)
from app.execution.sandbox_manager import SandboxManager


@pytest.fixture
def temp_repo(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("def hello():\n    return 'world'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text(
        "def test_broken():\n    assert False\n"
    )
    return tmp_path


def test_repo_analyzer_indexes_files(temp_repo):
    analysis = RepoAnalyzer.analyze(temp_repo, task_hint="fix failing tests")
    assert analysis.code_files >= 1
    assert analysis.test_files >= 1
    assert "python" in analysis.languages
    assert len(analysis.relevant_files) >= 1


def test_code_planner_generates_edit_plan(temp_repo):
    analysis = RepoAnalyzer.analyze(temp_repo, task_hint="fix failing tests")
    plan = CodePlanner.plan(analysis, "Fix the failing tests in this repository")
    assert plan.plan_id
    assert len(plan.edits) >= 1
    assert "pytest" in plan.test_command.lower()


def test_patch_executor_applies_and_rolls_back(temp_repo):
    executor = PatchExecutor(temp_repo)
    target = "tests/test_main.py"
    new_content = "def test_broken():\n    assert True\n"
    record = executor.apply_text_patch(target, new_content)
    assert record.success
    assert record.diff
    assert "assert True" in (temp_repo / target).read_text()
    assert executor.rollback(record.patch_id)
    assert "assert False" in (temp_repo / target).read_text()


async def _test_runner_detects_failure(temp_repo):
    result = await TestRunner.run(
        temp_repo,
        f"{sys.executable} -m pytest -q --tb=line",
        timeout=60,
    )
    assert not result.passed
    assert result.exit_code != 0


def test_test_runner_detects_failure(temp_repo):
    asyncio.run(_test_runner_detects_failure(temp_repo))


async def _debug_loop_retries(temp_repo):
    emitted = []

    async def emit_fn(eid, event_type, agent, message, **data):
        emitted.append({"type": event_type, "message": message})

    result = await DebugLoop.run(
        temp_repo,
        "Fix the failing tests",
        execution_id="test-exec",
        emit_fn=emit_fn,
        max_retries=2,
    )
    assert len(result.iterations) >= 1
    event_types = {e["type"] for e in emitted}
    assert "test_started" in event_types


def test_debug_loop_retries(temp_repo):
    asyncio.run(_debug_loop_retries(temp_repo))


def test_coding_objective_detection():
    assert is_coding_objective("Fix the failing tests in this repository")
    assert not is_coding_objective("Say hello")


def test_browser_objective_detection():
    assert is_browser_objective(
        "Open GitHub, research latest NVIDIA Blackwell news, and summarize findings"
    )


def test_apply_coding_profiles():
    nodes = [
        {
            "id": "agent-0",
            "type": "agent",
            "data": {"label": "Agent", "goal": "do work"},
            "position": {"x": 0, "y": 0},
        },
    ]
    updated = apply_coding_profiles(nodes, "Fix failing tests in repo")
    assert updated[0]["data"]["label"] == "RepoArchitect"


def test_sandbox_manager_workspace():
    SandboxManager.cleanup_all()
    ws = SandboxManager.create_workspace("exec-test-123")
    assert ws.workdir.exists()
    ok, used, limit = SandboxManager.check_quota("exec-test-123")
    assert ok
    assert SandboxManager.cleanup("exec-test-123", force=True)


def test_coding_orchestrator_is_coding_objective():
    assert CodingOrchestrator.is_coding_objective("Implement a new API endpoint")
