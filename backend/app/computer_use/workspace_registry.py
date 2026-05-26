"""
Workspace registry — snapshot and restore desktop workspace state.
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
class WorkspaceSnapshot:
    snapshot_id: str
    execution_id: str
    session_id: str
    label: str
    open_windows: list[str] = field(default_factory=list)
    active_window: str = ""
    workspace_path: str = ""
    image_path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "executionId": self.execution_id,
            "sessionId": self.session_id,
            "label": self.label,
            "openWindows": self.open_windows,
            "activeWindow": self.active_window,
            "workspacePath": self.workspace_path,
            "imagePath": self.image_path,
            "metadata": self.metadata,
            "createdAt": self.created_at,
        }


class WorkspaceRegistry:
    """Manage workspace snapshots for restore and replay."""

    _cache: dict[str, list[WorkspaceSnapshot]] = {}

    def __init__(self, execution_id: str, session_id: str = "") -> None:
        self.execution_id = execution_id
        self.session_id = session_id

    async def save_snapshot(
        self,
        label: str,
        *,
        open_windows: list[str] | None = None,
        active_window: str = "",
        workspace_path: str = "",
        image_path: str = "",
        metadata: dict[str, Any] | None = None,
        emit_fn: EmitFn | None = None,
    ) -> WorkspaceSnapshot:
        snap = WorkspaceSnapshot(
            snapshot_id=f"wsnap-{uuid.uuid4().hex[:10]}",
            execution_id=self.execution_id,
            session_id=self.session_id,
            label=label,
            open_windows=open_windows or [],
            active_window=active_window,
            workspace_path=workspace_path,
            image_path=image_path,
            metadata=metadata or {},
        )
        self._cache.setdefault(self.execution_id, []).append(snap)
        await self._persist(snap)

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "workspace_snapshot_saved",
                "DesktopNavigator",
                f"Workspace snapshot: {label}",
                snapshot=snap.to_dict(),
            )
        return snap

    async def list_snapshots(self, limit: int = 20) -> list[WorkspaceSnapshot]:
        from app.database.database import get_db

        db = await get_db()
        cursor = await db.execute(
            """SELECT id, session_id, label, snapshot_data, image_path, created_at
               FROM workspace_snapshots
               WHERE execution_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (self.execution_id, limit),
        )
        rows = await cursor.fetchall()
        result: list[WorkspaceSnapshot] = []
        for row in rows:
            data = json.loads(row[3] or "{}")
            result.append(WorkspaceSnapshot(
                snapshot_id=row[0],
                execution_id=self.execution_id,
                session_id=row[1],
                label=row[2],
                open_windows=data.get("openWindows", []),
                active_window=data.get("activeWindow", ""),
                workspace_path=data.get("workspacePath", ""),
                image_path=row[4],
                metadata=data.get("metadata", {}),
                created_at=row[5],
            ))
        return result

    async def restore_latest(self) -> WorkspaceSnapshot | None:
        snaps = await self.list_snapshots(limit=1)
        return snaps[0] if snaps else None

    async def _persist(self, snap: WorkspaceSnapshot) -> None:
        try:
            from app.database.database import get_db

            snapshot_data = {
                "openWindows": snap.open_windows,
                "activeWindow": snap.active_window,
                "workspacePath": snap.workspace_path,
                "metadata": snap.metadata,
            }
            db = await get_db()
            await db.execute(
                """INSERT INTO workspace_snapshots
                   (id, execution_id, session_id, label, snapshot_data, image_path, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    snap.snapshot_id,
                    self.execution_id,
                    snap.session_id,
                    snap.label,
                    json.dumps(snapshot_data),
                    snap.image_path,
                    snap.created_at,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.debug("Snapshot persist skipped: %s", exc)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._cache.pop(execution_id, None)
