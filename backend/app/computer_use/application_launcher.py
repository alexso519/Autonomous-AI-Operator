"""
Application launcher — start desktop applications safely.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

KNOWN_APPS: dict[str, dict[str, str]] = {
    "notepad": {"Windows": "notepad.exe", "Darwin": "open -a TextEdit", "Linux": "gedit"},
    "calculator": {"Windows": "calc.exe", "Darwin": "open -a Calculator", "Linux": "gnome-calculator"},
    "explorer": {"Windows": "explorer.exe", "Darwin": "open .", "Linux": "xdg-open ."},
    "browser": {"Windows": "start msedge", "Darwin": "open -a Safari", "Linux": "xdg-open https://localhost"},
}


@dataclass
class LaunchResult:
    app_name: str
    success: bool
    command: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "appName": self.app_name,
            "success": self.success,
            "command": self.command,
            "error": self.error,
        }


class ApplicationLauncher:
    """Launch desktop applications within sandbox constraints."""

    def __init__(self, *, allowed_apps: set[str] | None = None) -> None:
        self.allowed_apps = allowed_apps or set(KNOWN_APPS.keys()) | {"browser", "application"}

    def launch(self, app_name: str) -> LaunchResult:
        key = app_name.lower().strip()
        if key not in self.allowed_apps and key != "application":
            return LaunchResult(app_name, False, error=f"app_not_allowed:{key}")

        system = platform.system()
        mapping = KNOWN_APPS.get(key, {})
        command = mapping.get(system, "")
        if not command and key != "application":
            found = shutil.which(key)
            command = found or ""

        if not command:
            return LaunchResult(app_name, False, error="app_not_found")

        try:
            if system == "Windows" and command.endswith(".exe"):
                subprocess.Popen([command], shell=False)
            else:
                subprocess.Popen(command, shell=True)
            return LaunchResult(app_name, True, command=command)
        except Exception as exc:
            logger.warning("Launch failed for %s: %s", app_name, exc)
            return LaunchResult(app_name, False, command=command, error=str(exc))
