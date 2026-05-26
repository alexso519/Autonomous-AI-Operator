"""
Patch executor — apply edits safely with rollback and version history.
"""

from __future__ import annotations

import difflib
import logging
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PatchRecord:
    patch_id: str
    file_path: str
    action: str
    diff: str
    applied_at: str
    success: bool
    rollback_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "patchId": self.patch_id,
            "filePath": self.file_path,
            "action": self.action,
            "diff": self.diff,
            "appliedAt": self.applied_at,
            "success": self.success,
            "rollbackPath": self.rollback_path,
            "error": self.error,
        }


class PatchExecutor:
    """Apply file patches with automatic backup and rollback support."""

    def __init__(self, workspace: Path, backup_dir: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        self.backup_dir = backup_dir or (self.workspace / ".patch_backups")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.history: list[PatchRecord] = []

    def apply_text_patch(
        self,
        relative_path: str,
        new_content: str,
        *,
        action: str = "modify",
    ) -> PatchRecord:
        """Replace file content with backup for rollback."""
        patch_id = f"patch-{uuid.uuid4().hex[:8]}"
        target = self._resolve(relative_path)
        old_content = ""
        rollback_path: str | None = None

        try:
            if target.exists():
                old_content = target.read_text(encoding="utf-8", errors="replace")
                backup = self.backup_dir / f"{patch_id}-{target.name}"
                shutil.copy2(target, backup)
                rollback_path = str(backup)

            diff = self._make_diff(relative_path, old_content, new_content)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_content, encoding="utf-8")

            record = PatchRecord(
                patch_id=patch_id,
                file_path=relative_path,
                action=action,
                diff=diff,
                applied_at=datetime.now(timezone.utc).isoformat(),
                success=True,
                rollback_path=rollback_path,
            )
        except OSError as exc:
            record = PatchRecord(
                patch_id=patch_id,
                file_path=relative_path,
                action=action,
                diff="",
                applied_at=datetime.now(timezone.utc).isoformat(),
                success=False,
                error=str(exc),
            )
            logger.error("Patch failed for %s: %s", relative_path, exc)

        self.history.append(record)
        return record

    def rollback(self, patch_id: str) -> bool:
        """Rollback a specific patch using its backup."""
        record = next((r for r in self.history if r.patch_id == patch_id), None)
        if record is None or not record.rollback_path:
            return False

        backup = Path(record.rollback_path)
        target = self._resolve(record.file_path)
        if not backup.exists():
            return False

        try:
            shutil.copy2(backup, target)
            logger.info("Rolled back patch %s on %s", patch_id, record.file_path)
            return True
        except OSError as exc:
            logger.error("Rollback failed: %s", exc)
            return False

    def rollback_last(self) -> bool:
        """Rollback the most recent successful patch."""
        for record in reversed(self.history):
            if record.success and record.rollback_path:
                return self.rollback(record.patch_id)
        return False

    def rollback_all(self) -> int:
        """Rollback all patches in reverse order."""
        count = 0
        for record in reversed(self.history):
            if record.success and self.rollback(record.patch_id):
                count += 1
        return count

    def get_history(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.history]

    def _resolve(self, relative_path: str) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            return candidate
        return (self.workspace / relative_path).resolve()

    @staticmethod
    def _make_diff(path: str, old: str, new: str) -> str:
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines, new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
        return "".join(diff)[:8000]
