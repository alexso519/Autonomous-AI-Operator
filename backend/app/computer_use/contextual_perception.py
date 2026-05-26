"""
Contextual perception — cross-application contextual understanding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PerceptionContext:
    active_app: str = ""
    open_apps: list[str] = field(default_factory=list)
    screen_text: str = ""
    ui_elements: list[dict[str, Any]] = field(default_factory=list)
    recent_actions: list[str] = field(default_factory=list)
    cross_app_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activeApp": self.active_app,
            "openApps": self.open_apps,
            "screenTextExcerpt": self.screen_text[:200],
            "elementCount": len(self.ui_elements),
            "recentActions": self.recent_actions[-5:],
            "crossAppHints": self.cross_app_hints,
        }


class ContextualPerception:
    """Build contextual understanding across applications."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self._history: list[dict[str, Any]] = []

    def perceive(
        self,
        *,
        active_app: str = "",
        open_apps: list[str] | None = None,
        screen_text: str = "",
        ui_elements: list[dict[str, Any]] | None = None,
        recent_action: str = "",
    ) -> PerceptionContext:
        if recent_action:
            self._history.append({"action": recent_action, "app": active_app})

        hints = self._infer_cross_app_hints(active_app, open_apps or [], screen_text)

        return PerceptionContext(
            active_app=active_app,
            open_apps=open_apps or [],
            screen_text=screen_text,
            ui_elements=ui_elements or [],
            recent_actions=[h["action"] for h in self._history[-10:]],
            cross_app_hints=hints,
        )

    def _infer_cross_app_hints(
        self,
        active_app: str,
        open_apps: list[str],
        screen_text: str,
    ) -> list[str]:
        hints: list[str] = []
        text_lower = screen_text.lower()
        apps_lower = [a.lower() for a in open_apps]

        if any("browser" in a or "chrome" in a or "firefox" in a for a in apps_lower):
            if "download" in text_lower:
                hints.append("browser_download_pending")
        if any("terminal" in a or "powershell" in a or "cmd" in a for a in apps_lower):
            hints.append("terminal_available")
        if any("code" in a or "cursor" in a or "notepad" in a for a in apps_lower):
            hints.append("editor_available")
        if len(open_apps) > 2:
            hints.append("multi_app_workflow")
        return hints
