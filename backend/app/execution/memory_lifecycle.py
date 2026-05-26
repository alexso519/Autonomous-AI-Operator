"""
Memory lifecycle management — bounded retention, archival, cleanup.

No vector DB — SQLite-backed summarized archival.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)


class MemoryLifecycleManager:
    """Manage execution memory growth with retention policies."""

    @classmethod
    def retention_days(cls) -> int:
        return settings.memory_retention_days

    @classmethod
    def max_shared_memory_kb(cls) -> int:
        return settings.memory_max_shared_kb

    @classmethod
    async def compact_execution_memory(
        cls, execution_id: str, shared_memory: dict[str, Any]
    ) -> dict[str, Any]:
        """Produce a compact snapshot preserving key findings."""
        findings = shared_memory.get("findings") or shared_memory.get("key_findings") or []
        tool_calls = shared_memory.get("tool_calls") or []
        compact = {
            "findings": findings[-20:] if isinstance(findings, list) else findings,
            "toolCallCount": len(tool_calls) if isinstance(tool_calls, list) else 0,
            "lastTools": [
                t.get("tool_name", t.get("toolName", ""))
                for t in (tool_calls[-5:] if isinstance(tool_calls, list) else [])
            ],
            "workflowMemoryKeys": list(
                (shared_memory.get("workflow") or shared_memory).keys()
            )[:15],
            "compactedAt": datetime.now(timezone.utc).isoformat(),
        }
        await cls._persist_archival(execution_id, compact, full_size=len(json.dumps(shared_memory)))
        return compact

    @classmethod
    async def _persist_archival(
        cls, execution_id: str, snapshot: dict[str, Any], full_size: int
    ) -> None:
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT OR REPLACE INTO execution_memory_archive
                   (execution_id, snapshot_data, original_size_bytes, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    execution_id,
                    json.dumps(snapshot, ensure_ascii=False),
                    full_size,
                    now,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Archival persist failed: %s", exc)

    @classmethod
    async def expire_old_executions(cls) -> dict[str, int]:
        """Apply retention: trim shared_memory on old executions, delete stale logs."""
        stats = {"trimmed": 0, "archived": 0, "deleted_logs": 0}
        try:
            db = await get_db()
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=cls.retention_days())
            ).isoformat()

            cursor = await db.execute(
                """SELECT id, shared_memory FROM executions
                   WHERE status IN ('completed', 'failed', 'cancelled')
                   AND completed_at IS NOT NULL AND completed_at < ?""",
                (cutoff,),
            )
            rows = await cursor.fetchall()

            for row in rows:
                mem_raw = row["shared_memory"] or "{}"
                try:
                    mem = json.loads(mem_raw) if isinstance(mem_raw, str) else mem_raw
                except json.JSONDecodeError:
                    mem = {}
                if len(mem_raw) > cls.max_shared_memory_kb() * 1024:
                    compact = await cls.compact_execution_memory(row["id"], mem)
                    await db.execute(
                        "UPDATE executions SET shared_memory = ? WHERE id = ?",
                        (json.dumps(compact), row["id"]),
                    )
                    stats["trimmed"] += 1
                    stats["archived"] += 1

            # Delete old event logs beyond retention (keep summaries)
            log_cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=cls.retention_days() + 7)
            ).isoformat()
            cursor = await db.execute(
                """DELETE FROM execution_event_logs
                   WHERE execution_id IN (
                     SELECT id FROM executions
                     WHERE completed_at IS NOT NULL AND completed_at < ?
                   )""",
                (log_cutoff,),
            )
            stats["deleted_logs"] = cursor.rowcount or 0

            # Prune old checkpoints
            await db.execute(
                """DELETE FROM execution_checkpoints
                   WHERE created_at < ?""",
                (cutoff,),
            )

            await db.commit()
        except Exception as exc:
            logger.warning("Memory expiration failed: %s", exc)
        return stats

    @classmethod
    async def rolling_cleanup(cls) -> dict[str, int]:
        """Run full lifecycle cleanup — called on startup and periodically."""
        from app.tools.tool_efficiency import ToolEfficiencyLayer

        mem_stats = await cls.expire_old_executions()
        cache_pruned = await ToolEfficiencyLayer.prune_expired_cache()
        return {**mem_stats, "cachePruned": cache_pruned}

    @classmethod
    async def get_archived_memory(cls, execution_id: str) -> dict[str, Any] | None:
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT snapshot_data FROM execution_memory_archive WHERE execution_id = ?",
                (execution_id,),
            )
            row = await cursor.fetchone()
            if row:
                return json.loads(row["snapshot_data"] or "{}")
        except Exception as exc:
            logger.warning("Archive fetch failed: %s", exc)
        return None
