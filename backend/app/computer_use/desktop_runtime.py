"""
Native desktop runtime — multi-window desktop session with hybrid browser support.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.computer_use.application_launcher import ApplicationLauncher
from app.computer_use.application_state_store import ApplicationStateStore
from app.computer_use.desktop_state_tracker import DesktopStateTracker
from app.computer_use.file_interaction import FileInteraction
from app.computer_use.multimodal_loop import MultimodalLoop
from app.computer_use.runtime_guard import RuntimeGuard
from app.computer_use.session_manager import SessionManager
from app.computer_use.session_memory import SessionMemory
from app.computer_use.window_manager import WindowManager
from app.computer_use.workspace_registry import WorkspaceRegistry
from app.config.settings import settings
from app.execution.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class DesktopSession:
    session_id: str
    execution_id: str
    mode: str = "desktop"
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    interaction_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "executionId": self.execution_id,
            "mode": self.mode,
            "startedAt": self.started_at,
            "interactionCount": len(self.interaction_history),
        }


class DesktopRuntime:
    """Sandboxed desktop session with replayable interaction history."""

    _sessions: dict[str, DesktopSession] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.session_id = f"dsess-{uuid.uuid4().hex[:10]}"
        self.window_manager = WindowManager()
        self.launcher = ApplicationLauncher()
        self.state_tracker = DesktopStateTracker(execution_id)
        self.session_manager = SessionManager(execution_id)
        self.session_memory = SessionMemory(execution_id, self.session_id)
        self.app_state_store = ApplicationStateStore(execution_id, self.session_id)
        self.workspace_registry = WorkspaceRegistry(execution_id, self.session_id)
        self.runtime_guard = RuntimeGuard(execution_id)
        self.multimodal = MultimodalLoop(execution_id)
        ws = SandboxManager.get_desktop_workspace(execution_id)
        self.file_interaction = FileInteraction(ws.workdir)
        self._browser_runtime = None
        self._watchdog_task: asyncio.Task | None = None

    async def start(
        self,
        *,
        emit_fn: EmitFn | None = None,
        mode: str = "desktop",
    ) -> DesktopSession:
        persistent = await self.session_manager.start(mode=mode, emit_fn=emit_fn)
        self.session_id = persistent.session_id
        self.session_memory.session_id = persistent.session_id
        self.app_state_store.session_id = persistent.session_id
        self.workspace_registry.session_id = persistent.session_id

        session = DesktopSession(
            session_id=self.session_id,
            execution_id=self.execution_id,
            mode=mode,
        )
        self._sessions[self.execution_id] = session

        windows = self.window_manager.refresh()
        active = self.window_manager.active_window
        window_titles = [w.title for w in windows]
        self.state_tracker.update(
            active_window=active.title if active else "",
            open_windows=window_titles,
            workspace_path=str(self.file_interaction.workspace),
        )

        await self.app_state_store.sync_from_windows(
            [w.to_dict() for w in windows],
            active.title if active else "",
            emit_fn=emit_fn,
        )

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "desktop_state_updated",
                "DesktopNavigator",
                f"Desktop session started ({mode})",
                session=session.to_dict(),
                windows=self.window_manager.to_dict(),
                persistentSession=persistent.to_dict(),
            )

        self._watchdog_task = asyncio.create_task(self._watchdog(settings.ui_action_timeout_seconds))
        return session

    async def _watchdog(self, timeout: int) -> None:
        try:
            await asyncio.sleep(timeout)
            logger.info("Desktop watchdog timeout for %s", self.execution_id[:8])
        except asyncio.CancelledError:
            pass

    async def execute_objective(
        self,
        objective: str,
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        session = self._sessions.get(self.execution_id)
        if not session:
            await self.start(emit_fn=emit_fn)

        plan_mode = self.multimodal.cycle  # noqa: F841 — ensure cycle init
        from app.computer_use.action_planner import ActionPlanner
        plan = ActionPlanner.plan(objective)

        if plan.mode == "hybrid":
            result = await self._execute_hybrid(objective, plan, emit_fn=emit_fn)
        else:
            for step in plan.steps:
                if step.action == "launch":
                    launch = self.launcher.launch(step.target)
                    self._record_interaction({"type": "launch", **launch.to_dict()})
            result = await self.multimodal.run(objective, emit_fn=emit_fn, plan=plan)

        self.state_tracker.checkpoint("post_execution")
        await self.workspace_registry.save_snapshot(
            "post_execution",
            open_windows=self.state_tracker.state.open_windows,
            active_window=self.state_tracker.state.active_window,
            workspace_path=self.state_tracker.state.workspace_path,
            emit_fn=emit_fn,
        )
        self._record_interaction({"type": "objective_complete", "objective": objective[:200], **result})
        return result

    async def _execute_hybrid(
        self,
        objective: str,
        plan: Any,
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        """Browser + native app hybrid workflow."""
        browser_results: list[dict[str, Any]] = []
        for step in plan.steps:
            if step.action == "navigate" and step.target.startswith(("http", "www")):
                url = step.target if step.target.startswith("http") else f"https://{step.target}"
                try:
                    from app.browser.browser_runtime import BrowserRuntime
                    if self._browser_runtime is None:
                        self._browser_runtime = BrowserRuntime(
                            self.execution_id,
                            headless=settings.computer_use_headless,
                        )
                        await self._browser_runtime.start()
                    nav = await self._browser_runtime.navigate(url, emit_fn=emit_fn)
                    browser_results.append(nav.to_dict())
                except Exception as exc:
                    logger.warning("Hybrid browser step failed: %s", exc)
                    browser_results.append({"success": False, "error": str(exc)})

        desktop_result = await self.multimodal.run(objective, emit_fn=emit_fn, plan=plan)
        desktop_result["browserResults"] = browser_results
        desktop_result["mode"] = "hybrid"
        return desktop_result

    def _record_interaction(self, record: dict[str, Any]) -> None:
        session = self._sessions.get(self.execution_id)
        if session:
            session.interaction_history.append(record)

    def get_replay_history(self) -> list[dict[str, Any]]:
        session = self._sessions.get(self.execution_id)
        history = list(session.interaction_history) if session else []
        history.extend(self.multimodal.controller.get_replay_log())
        return history

    async def close(self) -> None:
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._browser_runtime:
            await self._browser_runtime.close()
        await self.session_manager.close()
        await self.multimodal.cleanup()
        DesktopStateTracker.cleanup(self.execution_id)
        SessionMemory.cleanup(self.execution_id)
        ApplicationStateStore.cleanup(self.execution_id)
        WorkspaceRegistry.cleanup(self.execution_id)
        RuntimeGuard.cleanup(self.execution_id)
        self._sessions.pop(self.execution_id, None)
