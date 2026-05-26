"""
Tests for persistent desktop sessions.
Run with: python -m pytest backend/tests/test_desktop_sessions.py -v
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.session_manager import SessionManager
from app.computer_use.session_memory import SessionMemory
from app.computer_use.workspace_registry import WorkspaceRegistry
from app.computer_use.application_state_store import ApplicationStateStore


async def _run_session_lifecycle():
    exec_id = "test-session-exec"
    emitted: list[str] = []

    async def emit_fn(eid, event_type, agent, message, **data):
        emitted.append(event_type)

    mgr = SessionManager(exec_id)
    session = await mgr.start(mode="desktop", emit_fn=emit_fn, restore=False)
    assert session.session_id.startswith("dsess-")
    assert "desktop_session_started" in emitted

    await mgr.update(active_app="Notepad", window_state={"count": 1})
    assert mgr.get_session() is not None

    memory = SessionMemory(exec_id, session.session_id)
    memory.remember("ui_state", {"label": "OK"}, fingerprint_source="ok-button")
    assert memory.last_ui_fingerprint()

    registry = WorkspaceRegistry(exec_id, session.session_id)
    snap = await registry.save_snapshot("test", active_window="Notepad", emit_fn=emit_fn)
    assert snap.snapshot_id
    assert "workspace_snapshot_saved" in emitted

    store = ApplicationStateStore(exec_id, session.session_id)
    await store.update_app("notepad", window_title="Untitled", focus_state="focused", emit_fn=emit_fn)
    assert store.active_app() is not None
    assert "application_state_updated" in emitted

    await mgr.close()
    SessionMemory.cleanup(exec_id)


def test_session_lifecycle():
    asyncio.run(_run_session_lifecycle())


def test_session_memory_recall():
    mem = SessionMemory("test-mem")
    mem.remember("action", {"type": "click"})
    mem.remember("ui_state", {"hash": "abc"}, fingerprint_source="abc")
    assert len(mem.recall("action")) == 1
    assert mem.last_ui_fingerprint() == mem.recall("ui_state")[0].fingerprint
    SessionMemory.cleanup("test-mem")
