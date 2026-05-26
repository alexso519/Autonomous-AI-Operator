"""
Tests for cross-application orchestration.
Run with: python -m pytest backend/tests/test_cross_application.py -v
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.application_orchestrator import ApplicationOrchestrator
from app.computer_use.file_workflow_engine import FileWorkflowEngine
from app.computer_use.system_interaction_layer import SystemInteractionLayer
from app.computer_use.runtime_guard import RuntimeGuard
from app.computer_use.human_override import HumanOverride


async def _run_app_orchestrator():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    orch = ApplicationOrchestrator("test-xapp")
    with patch("app.computer_use.application_launcher.ApplicationLauncher.launch") as mock_launch:
        mock_launch.return_value = type("R", (), {"success": True, "to_dict": lambda s: {"success": True}})()
        await orch.switch_application("notepad", emit_fn=emit_fn)
    assert "application_switched" in emitted
    ApplicationOrchestrator.cleanup("test-xapp")


def test_application_orchestrator():
    asyncio.run(_run_app_orchestrator())


async def _run_file_workflow():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    engine = FileWorkflowEngine("test-file-wf")
    result = await engine.run_file_workflow(
        "generate_report",
        content="# Test Report",
        emit_fn=emit_fn,
    )
    assert result.get("success") or result.get("error")
    assert "file_workflow_completed" in emitted


def test_file_workflow_engine():
    asyncio.run(_run_file_workflow())


def test_runtime_guard():
    guard = RuntimeGuard("test-guard")
    assert guard.record_action()
    assert guard.is_allowed()
    guard.emergency_stop("test")
    assert not guard.is_allowed()
    RuntimeGuard.cleanup("test-guard")


async def _run_human_override():
    emitted: list[str] = []

    async def emit_fn(eid, et, agent, msg, **data):
        emitted.append(et)

    override = HumanOverride("test-override")
    classification = await override.check_and_escalate(
        "type", "delete all files", emit_fn=emit_fn,
    )
    assert classification.danger_level == "dangerous"
    assert classification.requires_approval
    assert "approval_requested" in emitted
    HumanOverride.cleanup("test-override")


def test_human_override():
    asyncio.run(_run_human_override())


def test_system_interaction_classify():
    layer = SystemInteractionLayer("test-sys")
    itype = layer._classify_interaction("launch", "terminal", {})
    assert itype == "app_launch"
