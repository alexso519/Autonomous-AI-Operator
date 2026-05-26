"""
Desktop state tracker — active window, workspace, and checkpoint persistence.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class DesktopState:
    execution_id: str
    active_window: str = ""
    open_windows: list[str] = field(default_factory=list)
    clipboard_excerpt: str = ""
    workspace_path: str = ""
    last_snapshot_id: str = ""
    checkpoint_label: str = ""
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "activeWindow": self.active_window,
            "openWindows": self.open_windows,
            "clipboardExcerpt": self.clipboard_excerpt[:200],
            "workspacePath": self.workspace_path,
            "lastSnapshotId": self.last_snapshot_id,
            "checkpointLabel": self.checkpoint_label,
            "updatedAt": self.updated_at,
            "metadata": self.metadata,
        }


class DesktopStateTracker:
    """Track desktop session state with checkpointing."""

    _states: dict[str, DesktopState] = {}
    _checkpoint_dir = Path(__file__).resolve().parents[2] / "data" / "desktop_checkpoints"

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        if execution_id not in self._states:
            self._states[execution_id] = DesktopState(execution_id=execution_id)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

    @property
    def state(self) -> DesktopState:
        return self._states[self.execution_id]

    def update(
        self,
        *,
        active_window: str | None = None,
        open_windows: list[str] | None = None,
        clipboard: str | None = None,
        workspace_path: str | None = None,
        snapshot_id: str | None = None,
        **metadata: Any,
    ) -> DesktopState:
        s = self.state
        if active_window is not None:
            s.active_window = active_window
        if open_windows is not None:
            s.open_windows = open_windows
        if clipboard is not None:
            s.clipboard_excerpt = clipboard[:500]
        if workspace_path is not None:
            s.workspace_path = workspace_path
        if snapshot_id is not None:
            s.last_snapshot_id = snapshot_id
        s.metadata.update(metadata)
        s.updated_at = datetime.now(timezone.utc).isoformat()
        return s

    def checkpoint(self, label: str) -> Path:
        s = self.state
        s.checkpoint_label = label
        path = self._checkpoint_dir / f"{self.execution_id[:8]}_{label}.json"
        path.write_text(json.dumps(s.to_dict(), indent=2), encoding="utf-8")
        return path

    def detect_anomaly(self, previous_hash: str, current_hash: str) -> bool:
        if not previous_hash or not current_hash:
            return False
        return previous_hash != current_hash

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._states.pop(execution_id, None)
