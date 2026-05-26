"""
Window manager — track active and open windows (platform-aware with fallback).
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WindowInfo:
    title: str
    pid: int = 0
    is_active: bool = False
    bounds: tuple[int, int, int, int] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "pid": self.pid,
            "isActive": self.is_active,
            "bounds": list(self.bounds) if self.bounds else None,
        }


class WindowManager:
    """Multi-window tracking with platform-specific discovery."""

    def __init__(self) -> None:
        self._windows: list[WindowInfo] = []
        self._active: WindowInfo | None = None

    def refresh(self) -> list[WindowInfo]:
        system = platform.system()
        if system == "Windows":
            self._windows = self._list_windows_windows()
        elif system == "Darwin":
            self._windows = self._list_windows_macos()
        else:
            self._windows = self._list_windows_fallback()
        self._active = next((w for w in self._windows if w.is_active), self._windows[0] if self._windows else None)
        return self._windows

    def _list_windows_fallback(self) -> list[WindowInfo]:
        return [WindowInfo(title="Desktop", is_active=True)]

    def _list_windows_windows(self) -> list[WindowInfo]:
        try:
            ps = (
                "Get-Process | Where-Object {$_.MainWindowTitle} | "
                "Select-Object Id, MainWindowTitle | ConvertTo-Json"
            )
            result = subprocess.run(
                ["powershell", "-Command", ps],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return self._list_windows_fallback()
            import json
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            windows = [
                WindowInfo(title=(item.get("MainWindowTitle") or "Unknown"), pid=int(item.get("Id") or 0))
                for item in data if item.get("MainWindowTitle")
            ]
            if windows:
                windows[0].is_active = True
            return windows or self._list_windows_fallback()
        except Exception as exc:
            logger.debug("Windows enumeration failed: %s", exc)
            return self._list_windows_fallback()

    def _list_windows_macos(self) -> list[WindowInfo]:
        try:
            script = 'tell application "System Events" to get name of every process whose background only is false'
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
            if result.returncode != 0:
                return self._list_windows_fallback()
            names = [n.strip() for n in result.stdout.split(",") if n.strip()]
            windows = [WindowInfo(title=n, is_active=(i == 0)) for i, n in enumerate(names)]
            return windows or self._list_windows_fallback()
        except Exception as exc:
            logger.debug("macOS enumeration failed: %s", exc)
            return self._list_windows_fallback()

    @property
    def active_window(self) -> WindowInfo | None:
        return self._active

    @property
    def open_windows(self) -> list[WindowInfo]:
        return self._windows

    def to_dict(self) -> dict[str, Any]:
        return {
            "activeWindow": self._active.to_dict() if self._active else None,
            "windows": [w.to_dict() for w in self._windows],
        }
