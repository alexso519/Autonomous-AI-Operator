"""
Persistent cognitive memory — episodic, semantic, workflow patterns.

SQLite-compatible with compressed snapshots and retrieval scoring.
"""

from __future__ import annotations

import gzip
import base64
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)


def _compress(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=6)
    return base64.b64encode(compressed).decode("ascii")


def _decompress(blob: str) -> dict[str, Any]:
    try:
        raw = gzip.decompress(base64.b64decode(blob))
        return json.loads(raw)
    except Exception:
        return json.loads(blob) if blob.startswith("{") else {}


class CognitiveMemoryStore:
    """Cross-execution cognitive memory with retrieval scoring."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS cognitive_memories (
                id              TEXT PRIMARY KEY,
                memory_type     TEXT NOT NULL,
                execution_id    TEXT DEFAULT '',
                task_signature  TEXT NOT NULL DEFAULT '',
                content         TEXT NOT NULL DEFAULT '{}',
                snapshot_blob   TEXT DEFAULT '',
                retrieval_score REAL NOT NULL DEFAULT 0.5,
                use_count       INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_cognitive_memories_signature
               ON cognitive_memories(task_signature)"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_cognitive_memories_type
               ON cognitive_memories(memory_type)"""
        )
        await db.commit()

    @classmethod
    def task_signature(cls, objective: str) -> str:
        tokens = sorted(set(objective.lower().split()))[:40]
        return " ".join(tokens)[:500]

    @classmethod
    async def store_episodic(
        cls,
        execution_id: str,
        objective: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        await cls.ensure_tables()
        mem_id = f"ep-{uuid.uuid4().hex[:12]}"
        content = {
            "objective": objective[:1000],
            "outcome": outcome[:2000],
            "metadata": metadata or {},
        }
        snapshot = _compress(content)
        sig = cls.task_signature(objective)
        now = datetime.now(timezone.utc).isoformat()

        db = await get_db()
        await db.execute(
            """INSERT INTO cognitive_memories
               (id, memory_type, execution_id, task_signature, content,
                snapshot_blob, retrieval_score, use_count, created_at, updated_at)
               VALUES (?, 'episodic', ?, ?, ?, ?, 0.6, 0, ?, ?)""",
            (mem_id, execution_id, sig, json.dumps(content), snapshot, now, now),
        )
        await db.commit()
        return mem_id

    @classmethod
    async def store_semantic(
        cls,
        concept: str,
        knowledge: str,
        source_execution_id: str = "",
    ) -> str:
        await cls.ensure_tables()
        mem_id = f"sem-{uuid.uuid4().hex[:12]}"
        content = {"concept": concept, "knowledge": knowledge[:3000]}
        sig = cls.task_signature(concept)
        now = datetime.now(timezone.utc).isoformat()

        db = await get_db()
        await db.execute(
            """INSERT INTO cognitive_memories
               (id, memory_type, execution_id, task_signature, content,
                snapshot_blob, retrieval_score, use_count, created_at, updated_at)
               VALUES (?, 'semantic', ?, ?, ?, '', 0.7, 0, ?, ?)""",
            (mem_id, source_execution_id, sig, json.dumps(content), now, now),
        )
        await db.commit()
        return mem_id

    @classmethod
    async def store_workflow_pattern(
        cls,
        pattern_name: str,
        pattern_data: dict[str, Any],
        execution_id: str = "",
    ) -> str:
        await cls.ensure_tables()
        mem_id = f"wf-{uuid.uuid4().hex[:12]}"
        content = {"name": pattern_name, "pattern": pattern_data}
        sig = cls.task_signature(pattern_name + " " + json.dumps(pattern_data)[:200])
        snapshot = _compress(content)
        now = datetime.now(timezone.utc).isoformat()

        db = await get_db()
        await db.execute(
            """INSERT INTO cognitive_memories
               (id, memory_type, execution_id, task_signature, content,
                snapshot_blob, retrieval_score, use_count, created_at, updated_at)
               VALUES (?, 'workflow', ?, ?, ?, ?, 0.75, 0, ?, ?)""",
            (mem_id, execution_id, sig, json.dumps(content), snapshot, now, now),
        )
        await db.commit()
        return mem_id

    @classmethod
    async def store_recovery_strategy(
        cls,
        failure_type: str,
        strategy: dict[str, Any],
        execution_id: str = "",
    ) -> str:
        await cls.ensure_tables()
        mem_id = f"rec-{uuid.uuid4().hex[:12]}"
        content = {"failureType": failure_type, "strategy": strategy}
        sig = failure_type
        now = datetime.now(timezone.utc).isoformat()

        db = await get_db()
        await db.execute(
            """INSERT INTO cognitive_memories
               (id, memory_type, execution_id, task_signature, content,
                snapshot_blob, retrieval_score, use_count, created_at, updated_at)
               VALUES (?, 'recovery', ?, ?, ?, '', 0.65, 0, ?, ?)""",
            (mem_id, execution_id, sig, json.dumps(content), now, now),
        )
        await db.commit()
        return mem_id

    @classmethod
    async def retrieve(
        cls,
        objective: str,
        memory_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        sig = cls.task_signature(objective)
        sig_tokens = set(sig.split())

        db = await get_db()
        types = memory_types or ["episodic", "semantic", "workflow", "recovery"]
        placeholders = ",".join("?" * len(types))
        cursor = await db.execute(
            f"""SELECT * FROM cognitive_memories
                WHERE memory_type IN ({placeholders})
                ORDER BY retrieval_score DESC, use_count DESC
                LIMIT 50""",
            types,
        )
        rows = await cursor.fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            row_sig_tokens = set((row["task_signature"] or "").split())
            overlap = len(sig_tokens & row_sig_tokens) / max(len(sig_tokens), 1)
            score = float(row["retrieval_score"]) * 0.5 + overlap * 0.5
            content = json.loads(row["content"] or "{}")
            if row["snapshot_blob"]:
                try:
                    content = _decompress(row["snapshot_blob"])
                except Exception:
                    pass
            scored.append(
                (
                    score,
                    {
                        "id": row["id"],
                        "memoryType": row["memory_type"],
                        "executionId": row["execution_id"],
                        "retrievalScore": round(score, 3),
                        "content": content,
                        "useCount": row["use_count"],
                    },
                )
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:limit]]

        # Increment use_count for retrieved items
        for r in results:
            await db.execute(
                "UPDATE cognitive_memories SET use_count = use_count + 1 WHERE id = ?",
                (r["id"],),
            )
        if results:
            await db.commit()

        return results

    @classmethod
    async def get_execution_memories(cls, execution_id: str) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT * FROM cognitive_memories
               WHERE execution_id = ?
               ORDER BY created_at DESC""",
            (execution_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "memoryType": r["memory_type"],
                "taskSignature": r["task_signature"],
                "content": json.loads(r["content"] or "{}"),
                "retrievalScore": r["retrieval_score"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
