"""
Vector memory store — semantic long-term memory with pgvector-ready schema.

SQLite stores embeddings as JSON blobs; pgvector column is optional.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.intelligence.embedding_service import EmbeddingService, cosine_similarity

logger = logging.getLogger(__name__)


class VectorMemory:
    """Persistent semantic memory with embedding-backed retrieval."""

    MEMORY_TYPES = (
        "execution",
        "tool_result",
        "research",
        "strategy",
        "pattern",
        "entity",
    )

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS vector_memories (
                id              TEXT PRIMARY KEY,
                memory_type     TEXT NOT NULL,
                execution_id    TEXT DEFAULT '',
                source_id       TEXT DEFAULT '',
                content         TEXT NOT NULL,
                content_hash    TEXT NOT NULL DEFAULT '',
                embedding_blob  TEXT NOT NULL DEFAULT '[]',
                metadata        TEXT NOT NULL DEFAULT '{}',
                retrieval_score REAL NOT NULL DEFAULT 0.5,
                use_count       INTEGER NOT NULL DEFAULT 0,
                privacy_tag     TEXT DEFAULT 'internal',
                created_at      TEXT NOT NULL,
                expires_at      TEXT DEFAULT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS semantic_chunks (
                id              TEXT PRIMARY KEY,
                parent_id       TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL DEFAULT 0,
                content         TEXT NOT NULL,
                embedding_blob  TEXT NOT NULL DEFAULT '[]',
                metadata        TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES vector_memories(id)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS retrieval_history (
                id              TEXT PRIMARY KEY,
                query           TEXT NOT NULL,
                execution_id    TEXT DEFAULT '',
                results_count   INTEGER NOT NULL DEFAULT 0,
                top_score       REAL NOT NULL DEFAULT 0,
                memory_ids      TEXT NOT NULL DEFAULT '[]',
                context_tags    TEXT NOT NULL DEFAULT '[]',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_vector_memories_type
               ON vector_memories(memory_type)"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_vector_memories_hash
               ON vector_memories(content_hash)"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_semantic_chunks_parent
               ON semantic_chunks(parent_id)"""
        )
        await db.commit()
        await EmbeddingService.ensure_tables()

    @classmethod
    async def store(
        cls,
        content: str,
        memory_type: str,
        *,
        execution_id: str = "",
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
        privacy_tag: str = "internal",
        embed: bool = True,
    ) -> str | None:
        if not settings.enable_vector_memory or not content.strip():
            return None

        from app.intelligence.memory_safety import MemorySafety

        if not MemorySafety.passes_privacy_filter(content, privacy_tag):
            return None

        await cls.ensure_tables()

        dup_hash = await EmbeddingService.deduplicate_check(content)
        if dup_hash:
            db = await get_db()
            cursor = await db.execute(
                "SELECT id FROM vector_memories WHERE content_hash = ? LIMIT 1",
                (dup_hash,),
            )
            row = await cursor.fetchone()
            if row:
                return row["id"]

        mem_id = f"vm-{uuid.uuid4().hex[:12]}"
        embedding = await EmbeddingService.embed(content) if embed else []
        content_hash = EmbeddingService._content_hash(content, settings.embedding_model)
        now = datetime.now(timezone.utc).isoformat()
        expires = MemorySafety.compute_expiry(now)

        db = await get_db()
        await db.execute(
            """INSERT INTO vector_memories
               (id, memory_type, execution_id, source_id, content, content_hash,
                embedding_blob, metadata, retrieval_score, use_count, privacy_tag,
                created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.5, 0, ?, ?, ?)""",
            (
                mem_id,
                memory_type,
                execution_id,
                source_id,
                content[:8000],
                content_hash,
                json.dumps(embedding),
                json.dumps(metadata or {}),
                privacy_tag,
                now,
                expires,
            ),
        )
        await db.commit()
        await cls._enforce_retention_limit()
        return mem_id

    @classmethod
    async def _enforce_retention_limit(cls) -> None:
        max_mem = settings.max_long_term_memories
        db = await get_db()
        cursor = await db.execute("SELECT COUNT(*) as cnt FROM vector_memories")
        row = await cursor.fetchone()
        if row and row["cnt"] > max_mem:
            overflow = row["cnt"] - max_mem
            await db.execute(
                """DELETE FROM vector_memories WHERE id IN (
                       SELECT id FROM vector_memories
                       ORDER BY use_count ASC, created_at ASC
                       LIMIT ?
                   )""",
                (overflow,),
            )
            await db.commit()

    @classmethod
    async def search(
        cls,
        query: str,
        *,
        memory_types: list[str] | None = None,
        limit: int = 5,
        min_score: float = 0.05,
    ) -> list[dict[str, Any]]:
        if not settings.enable_vector_memory or not query.strip():
            return []

        from app.intelligence.memory_safety import MemorySafety

        limit = min(limit, MemorySafety.retrieval_budget())
        await cls.ensure_tables()

        query_emb = await EmbeddingService.embed(query)
        types = memory_types or list(cls.MEMORY_TYPES)
        placeholders = ",".join("?" * len(types))

        db = await get_db()
        cursor = await db.execute(
            f"""SELECT * FROM vector_memories
                WHERE memory_type IN ({placeholders})
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                ORDER BY created_at DESC
                LIMIT 500""",
            types,
        )
        rows = await cursor.fetchall()

        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            if not MemorySafety.passes_privacy_filter(row["content"], row["privacy_tag"]):
                continue
            try:
                emb = json.loads(row["embedding_blob"] or "[]")
            except json.JSONDecodeError:
                emb = []
            sim = cosine_similarity(query_emb, emb) if emb else 0.0
            combined = sim * 0.7 + float(row["retrieval_score"]) * 0.2 + min(row["use_count"], 10) * 0.01
            if combined >= min_score:
                scored.append(
                    (
                        combined,
                        {
                            "id": row["id"],
                            "memoryType": row["memory_type"],
                            "executionId": row["execution_id"],
                            "content": row["content"],
                            "similarity": round(sim, 4),
                            "combinedScore": round(combined, 4),
                            "metadata": json.loads(row["metadata"] or "{}"),
                            "useCount": row["use_count"],
                            "createdAt": row["created_at"],
                        },
                    )
                )

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item for _, item in scored[:limit]]

        for r in results:
            await db.execute(
                "UPDATE vector_memories SET use_count = use_count + 1 WHERE id = ?",
                (r["id"],),
            )

        hist_id = f"rh-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO retrieval_history
               (id, query, results_count, top_score, memory_ids, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                hist_id,
                query[:500],
                len(results),
                results[0]["combinedScore"] if results else 0,
                json.dumps([r["id"] for r in results]),
                now,
            ),
        )
        await db.commit()
        return results

    @classmethod
    async def get_by_execution(cls, execution_id: str) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, memory_type, content, metadata, created_at
               FROM vector_memories WHERE execution_id = ?
               ORDER BY created_at DESC""",
            (execution_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "memoryType": r["memory_type"],
                "content": r["content"][:300],
                "metadata": json.loads(r["metadata"] or "{}"),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    @classmethod
    async def compact_expired(cls) -> int:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """DELETE FROM vector_memories
               WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"""
        )
        await db.commit()
        return cursor.rowcount
