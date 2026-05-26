"""
File interaction — clipboard, file picker simulation, upload/download within sandbox.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FileActionResult:
    action: str
    path: str
    success: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "path": self.path,
            "success": self.success,
            "error": self.error,
        }


class FileInteraction:
    """Filesystem and clipboard operations scoped to sandbox workspace."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.downloads = self.workspace / "downloads"
        self.uploads = self.workspace / "uploads"
        self.downloads.mkdir(exist_ok=True)
        self.uploads.mkdir(exist_ok=True)

    def read_clipboard(self) -> str:
        try:
            import pyperclip  # type: ignore
            return pyperclip.paste() or ""
        except ImportError:
            return ""
        except Exception as exc:
            logger.debug("Clipboard read failed: %s", exc)
            return ""

    def write_clipboard(self, text: str) -> bool:
        try:
            import pyperclip  # type: ignore
            pyperclip.copy(text)
            return True
        except Exception:
            return False

    def copy_to_workspace(self, source: Path, dest_name: str | None = None) -> FileActionResult:
        if not source.exists():
            return FileActionResult("copy", str(source), False, error="source_not_found")
        try:
            resolved = source.resolve()
            if not str(resolved).startswith(str(self.workspace.resolve())):
                if source.stat().st_size > 10 * 1024 * 1024:
                    return FileActionResult("copy", str(source), False, error="file_too_large")
            dest = self.downloads / (dest_name or source.name)
            shutil.copy2(source, dest)
            return FileActionResult("copy", str(dest), True)
        except Exception as exc:
            return FileActionResult("copy", str(source), False, error=str(exc))

    def list_workspace_files(self) -> list[str]:
        return [str(p.relative_to(self.workspace)) for p in self.workspace.rglob("*") if p.is_file()][:100]

    def stage_upload(self, filename: str, content: bytes) -> FileActionResult:
        dest = self.uploads / filename
        try:
            dest.write_bytes(content)
            return FileActionResult("upload", str(dest), True)
        except Exception as exc:
            return FileActionResult("upload", str(dest), False, error=str(exc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "downloads": str(self.downloads),
            "uploads": str(self.uploads),
            "files": self.list_workspace_files(),
        }
