"""
Conflict resolver — contradiction clustering and escalation controls.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.memory_safety import MemorySafety

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class ConflictResolver:
    """Cluster and resolve knowledge contradictions."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_conflicts (
                id              TEXT PRIMARY KEY,
                subject         TEXT NOT NULL,
                claim_a         TEXT NOT NULL,
                claim_b         TEXT NOT NULL,
                cluster_id      TEXT DEFAULT '',
                conflict_score  REAL NOT NULL DEFAULT 0.5,
                status          TEXT NOT NULL DEFAULT 'open',
                resolution      TEXT DEFAULT '',
                execution_id    TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                resolved_at     TEXT
            )"""
        )
        await db.commit()

    @classmethod
    def _conflict_score(cls, a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        a_set = set(a.lower().split())
        b_set = set(b.lower().split())
        if not a_set or not b_set:
            return 0.5
        overlap = len(a_set & b_set) / max(len(a_set | b_set), 1)
        return min(1.0, 1.0 - overlap + 0.3)

    @classmethod
    async def register_conflict(
        cls,
        subject: str,
        claim_a: str,
        claim_b: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any] | None:
        if not settings.enable_vector_memory:
            return None

        score = cls._conflict_score(claim_a, claim_b)
        if score < 0.35:
            return None

        await cls.ensure_tables()
        cluster_id = f"clu-{hash(subject.lower()) % 100000:05d}"
        now = datetime.now(timezone.utc).isoformat()
        cid = f"kcf-{uuid.uuid4().hex[:12]}"
        db = await get_db()

        cursor = await db.execute(
            """SELECT COUNT(*) AS cnt FROM knowledge_conflicts
               WHERE subject = ? AND status = 'open'""",
            (subject,),
        )
        row = await cursor.fetchone()
        open_count = int(row["cnt"] if row else 0)
        if open_count >= MemorySafety.contradiction_escalation_limit():
            return {"status": "escalation_blocked", "openConflicts": open_count}

        await db.execute(
            """INSERT INTO knowledge_conflicts
               (id, subject, claim_a, claim_b, cluster_id, conflict_score,
                status, execution_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
            (cid, subject[:120], claim_a[:400], claim_b[:400], cluster_id, score, execution_id, now),
        )
        await db.commit()
        return {"id": cid, "subject": subject, "conflictScore": score, "clusterId": cluster_id}

    @classmethod
    async def resolve_conflict(
        cls,
        conflict_id: str,
        resolution: str,
        *,
        winning_claim: str = "",
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any] | None:
        if not settings.enable_vector_memory:
            return None

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            """UPDATE knowledge_conflicts SET status = 'resolved', resolution = ?,
               resolved_at = ? WHERE id = ?""",
            (resolution[:500], now, conflict_id),
        )
        await db.commit()

        if emit_fn:
            await emit_fn(
                execution_id,
                "contradiction_resolved",
                "intelligence",
                f"Contradiction resolved: {resolution[:80]}",
                conflictId=conflict_id,
                winningClaim=winning_claim[:200],
            )
        return {"conflictId": conflict_id, "resolution": resolution, "winningClaim": winning_claim}

    @classmethod
    async def get_open_conflicts(cls, limit: int = 20) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, subject, claim_a, claim_b, cluster_id, conflict_score, status, created_at
               FROM knowledge_conflicts WHERE status = 'open'
               ORDER BY conflict_score DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "subject": r["subject"],
                "claimA": r["claim_a"],
                "claimB": r["claim_b"],
                "clusterId": r["cluster_id"],
                "conflictScore": r["conflict_score"],
                "status": r["status"],
                "createdAt": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]
