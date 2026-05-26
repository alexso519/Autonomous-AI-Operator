"""
Execution sandbox manager — isolated workspaces, temp clones, quotas, cleanup.

Provides per-execution workdirs with filesystem quotas and provenance tracking.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SANDBOX_ROOT = PROJECT_ROOT / "data" / "sandboxes"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB per workspace


@dataclass
class SandboxWorkspace:
    execution_id: str
    workdir: Path
    persistent: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    max_bytes: int = DEFAULT_MAX_BYTES
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "workdir": str(self.workdir),
            "persistent": self.persistent,
            "createdAt": self.created_at,
            "maxBytes": self.max_bytes,
            "metadata": self.metadata,
        }


class SandboxManager:
    """Manage isolated execution workspaces with quotas and cleanup."""

    _workspaces: dict[str, SandboxWorkspace] = {}

    @classmethod
    def sandbox_root(cls) -> Path:
        root = DEFAULT_SANDBOX_ROOT
        root.mkdir(parents=True, exist_ok=True)
        return root

    @classmethod
    def create_workspace(
        cls,
        execution_id: str,
        *,
        persistent: bool = False,
        clone_from: Path | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> SandboxWorkspace:
        """Create an isolated workspace for an execution."""
        if execution_id in cls._workspaces:
            return cls._workspaces[execution_id]

        workdir = cls.sandbox_root() / execution_id[:12] / uuid.uuid4().hex[:8]
        workdir.mkdir(parents=True, exist_ok=True)

        if clone_from and clone_from.exists():
            cls._copy_tree_safe(clone_from, workdir, max_bytes)

        ws = SandboxWorkspace(
            execution_id=execution_id,
            workdir=workdir,
            persistent=persistent,
            max_bytes=max_bytes,
        )
        cls._workspaces[execution_id] = ws
        logger.info("Created sandbox workspace %s at %s", execution_id[:8], workdir)
        return ws

    @classmethod
    def get_workspace(cls, execution_id: str) -> SandboxWorkspace | None:
        return cls._workspaces.get(execution_id)

    @classmethod
    def get_or_create(
        cls,
        execution_id: str,
        *,
        clone_from: Path | None = None,
    ) -> SandboxWorkspace:
        existing = cls.get_workspace(execution_id)
        if existing:
            return existing
        return cls.create_workspace(execution_id, clone_from=clone_from)

    @classmethod
    def get_desktop_workspace(cls, execution_id: str) -> SandboxWorkspace:
        """Isolated workspace for desktop file interactions."""
        ws = cls.get_or_create(execution_id)
        desktop_dir = ws.workdir / "desktop"
        desktop_dir.mkdir(parents=True, exist_ok=True)
        ws.metadata["desktopDir"] = str(desktop_dir)
        return ws

    @classmethod
    def workspace_size(cls, workdir: Path) -> int:
        total = 0
        if not workdir.exists():
            return 0
        for p in workdir.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    @classmethod
    def check_quota(cls, execution_id: str) -> tuple[bool, int, int]:
        ws = cls.get_workspace(execution_id)
        if ws is None:
            return True, 0, DEFAULT_MAX_BYTES
        used = cls.workspace_size(ws.workdir)
        return used <= ws.max_bytes, used, ws.max_bytes

    @classmethod
    def checkpoint(cls, execution_id: str, label: str) -> Path | None:
        """Create a checkpoint snapshot of the workspace."""
        ws = cls.get_workspace(execution_id)
        if ws is None:
            return None
        checkpoint_dir = ws.workdir.parent / f"checkpoint-{label}-{uuid.uuid4().hex[:6]}"
        if ws.workdir.exists():
            shutil.copytree(ws.workdir, checkpoint_dir, dirs_exist_ok=True)
        ws.metadata.setdefault("checkpoints", []).append(
            {"label": label, "path": str(checkpoint_dir)}
        )
        return checkpoint_dir

    @classmethod
    def cleanup(cls, execution_id: str, *, force: bool = False) -> bool:
        """Remove workspace unless marked persistent."""
        ws = cls._workspaces.pop(execution_id, None)
        if ws is None:
            return False
        if ws.persistent and not force:
            logger.info("Skipping cleanup of persistent workspace %s", execution_id[:8])
            return False
        if ws.workdir.exists():
            shutil.rmtree(ws.workdir, ignore_errors=True)
        parent = ws.workdir.parent
        if parent.exists() and not any(parent.iterdir()):
            shutil.rmtree(parent, ignore_errors=True)
        logger.info("Cleaned up sandbox workspace %s", execution_id[:8])
        return True

    @classmethod
    def cleanup_all(cls) -> int:
        count = 0
        for eid in list(cls._workspaces.keys()):
            if cls.cleanup(eid, force=True):
                count += 1
        return count

    @classmethod
    def _copy_tree_safe(cls, src: Path, dst: Path, max_bytes: int) -> None:
        """Copy source tree respecting quota."""
        copied = 0
        skip_dirs = {".git", "__pycache__", "node_modules", ".pytest_cache", "venv", ".venv"}

        for item in src.rglob("*"):
            if any(part in skip_dirs for part in item.parts):
                continue
            rel = item.relative_to(src)
            target = dst / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                size = item.stat().st_size
                if copied + size > max_bytes:
                    logger.warning("Sandbox quota reached during clone; truncating copy")
                    return
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
                copied += size
