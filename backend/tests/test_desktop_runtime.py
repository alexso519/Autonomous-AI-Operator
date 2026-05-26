"""
Tests for desktop runtime.
Run with: python -m pytest backend/tests/test_desktop_runtime.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.application_launcher import ApplicationLauncher
from app.computer_use.computer_use_agents import (
    is_computer_use_pipeline_objective,
    is_desktop_objective,
)
from app.computer_use.desktop_runtime import DesktopRuntime
from app.computer_use.window_manager import WindowManager


def test_is_desktop_objective():
    assert is_desktop_objective("Click the OK button on screen")
    assert is_computer_use_pipeline_objective("Take a screenshot of the desktop")
    assert not is_desktop_objective("Summarize this article")


def test_window_manager_refresh():
    wm = WindowManager()
    windows = wm.refresh()
    assert len(windows) >= 1


def test_application_launcher_not_allowed():
    launcher = ApplicationLauncher(allowed_apps={"notepad"})
    result = launcher.launch("unknown_app_xyz")
    assert not result.success


async def _run_desktop_runtime_session():
    runtime = DesktopRuntime("test-exec-desktop")
    emitted: list[str] = []

    async def emit_fn(exec_id, event_type, agent, message, **data):
        emitted.append(event_type)

    session = await runtime.start(emit_fn=emit_fn)
    assert session.session_id
    assert "desktop_state_updated" in emitted

    result = await runtime.execute_objective(
        "Analyze the current screen",
        emit_fn=emit_fn,
    )
    assert "results" in result
    await runtime.close()


def test_desktop_runtime_session():
    asyncio.run(_run_desktop_runtime_session())


def test_desktop_runtime_sync():
    asyncio.run(_run_desktop_runtime_session())
