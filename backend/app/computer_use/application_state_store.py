"""
Application state store — multi-window and cross-app state persistence.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ApplicationState:
    app_name: str
    window_title: str = ""
    focus_state: str = "background"
    pid: int = 0
    bounds: list[int] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "appName": self.app_name,
            "windowTitle": self.window_title,
            "focusState": self.focus_state,
            "pid": self.pid,
            "bounds": self.bounds,
            "metadata": self.metadata,
            "updatedAt": self.updated_at,
        }


class ApplicationStateStore:
    """Track active applications and window focus across a desktop session."""

    _states: dict[str, dict[str, ApplicationState]] = {}

    def __init__(self, execution_id: str, session_id: str = "") -> None:
        self.execution_id = execution_id
        self.session_id = session_id
        if execution_id not in self._states:
            self._states[execution_id] = {}

    @property
    def apps(self) -> dict[str, ApplicationState]:
        return self._states.get(self.execution_id, {})

    def active_app(self) -> ApplicationState | None:
        for app in self.apps.values():
            if app.focus_state == "focused":
                return app
        return None

    async def update_app(
        self,
        app_name: str,
        *,
        window_title: str | None = None,
        focus_state: str | None = None,
        pid: int | None = None,
        bounds: list[int] | None = None,
        metadata: dict[str, Any] | None = None,
        emit_fn: EmitFn | None = None,
    ) -> ApplicationState:
        store = self._states.setdefault(self.execution_id, {})
        existing = store.get(app_name)
        if existing:
            if window_title is not None:
                existing.window_title = window_title
            if focus_state is not None:
                if focus_state == "focused":
                    for a in store.values():
                        a.focus_state = "background"
                existing.focus_state = focus_state
            if pid is not None:
                existing.pid = pid
            if bounds is not None:
                existing.bounds = bounds
            if metadata:
                existing.metadata.update(metadata)
            existing.updated_at = _now()
            state = existing
        else:
            state = ApplicationState(
                app_name=app_name,
                window_title=window_title or "",
                focus_state=focus_state or "background",
                pid=pid or 0,
                bounds=bounds or [],
                metadata=metadata or {},
            )
            store[app_name] = state

        await self._persist(state)
        if emit_fn:
            await emit_fn(
                self.execution_id,
                "application_state_updated",
                "DesktopNavigator",
                f"App state: {app_name} ({state.focus_state})",
                application=state.to_dict(),
                activeApps=[a.to_dict() for a in store.values()],
            )
        return state

    async def sync_from_windows(
        self,
        windows: list[dict[str, Any]],
        active_title: str = "",
        *,
        emit_fn: EmitFn | None = None,
    ) -> list[ApplicationState]:
        updated: list[ApplicationState] = []
        for w in windows:
            title = w.get("title", "")
            app = w.get("app", title.split(" - ")[-1] if " - " in title else "unknown")
            focus = "focused" if title == active_title else "background"
            state = await self.update_app(
                app,
                window_title=title,
                focus_state=focus,
                bounds=w.get("bounds", []),
                emit_fn=None,
            )
            updated.append(state)
        if emit_fn and updated:
            await emit_fn(
                self.execution_id,
                "application_state_updated",
                "DesktopNavigator",
                f"Synced {len(updated)} application(s)",
                activeApps=[a.to_dict() for a in updated],
            )
        return updated

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "sessionId": self.session_id,
            "applications": [a.to_dict() for a in self.apps.values()],
            "activeApp": self.active_app().to_dict() if self.active_app() else None,
        }

    async def _persist(self, state: ApplicationState) -> None:
        try:
            from app.database.database import get_db

            db = await get_db()
            row_id = f"app-{uuid.uuid4().hex[:10]}"
            await db.execute(
                """INSERT INTO application_states
                   (id, execution_id, session_id, app_name, window_title, focus_state,
                    state_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row_id,
                    self.execution_id,
                    self.session_id,
                    state.app_name,
                    state.window_title,
                    state.focus_state,
                    json.dumps({"pid": state.pid, "bounds": state.bounds, "metadata": state.metadata}),
                    _now(),
                    state.updated_at,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.debug("App state persist skipped: %s", exc)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._states.pop(execution_id, None)
