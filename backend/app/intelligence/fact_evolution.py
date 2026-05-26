"""
Fact evolution — track knowledge facts with versioning and contradiction detection.
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


class FactEvolution:
    """Persistent facts with temporal evolution and contradiction handling."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS knowledge_facts (
                id              TEXT PRIMARY KEY,
                subject         TEXT NOT NULL,
                predicate       TEXT NOT NULL DEFAULT 'is',
                object_value    TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                freshness_score REAL NOT NULL DEFAULT 1.0,
                source_execution_id TEXT DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'active',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS fact_versions (
                id              TEXT PRIMARY KEY,
                fact_id         TEXT NOT NULL,
                version         INTEGER NOT NULL DEFAULT 1,
                object_value    TEXT NOT NULL,
                confidence      REAL NOT NULL DEFAULT 0.5,
                change_reason   TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (fact_id) REFERENCES knowledge_facts(id)
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_knowledge_facts_subject
               ON knowledge_facts(subject)"""
        )
        await db.commit()

    @classmethod
    async def upsert_fact(
        cls,
        subject: str,
        object_value: str,
        *,
        predicate: str = "is",
        confidence: float = 0.6,
        source_execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_vector_memory:
            return {"status": "disabled"}

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()

        cursor = await db.execute(
            """SELECT id, object_value, confidence FROM knowledge_facts
               WHERE lower(subject) = lower(?) AND predicate = ?
               LIMIT 1""",
            (subject, predicate),
        )
        row = await cursor.fetchone()

        if row:
            fact_id = row["id"]
            old_value = row["object_value"]
            cursor_v = await db.execute(
                "SELECT MAX(version) as max_v FROM fact_versions WHERE fact_id = ?",
                (fact_id,),
            )
            vrow = await cursor_v.fetchone()
            version = (vrow["max_v"] or 0) + 1 if vrow else 1
            if old_value.lower() != object_value.lower():
                contradiction = cls._detect_contradiction(old_value, object_value)
                ver_id = f"fv-{uuid.uuid4().hex[:12]}"
                await db.execute(
                    """INSERT INTO fact_versions
                       (id, fact_id, version, object_value, confidence, change_reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ver_id,
                        fact_id,
                        version,
                        object_value,
                        confidence,
                        "contradiction" if contradiction else "evolution",
                        now,
                    ),
                )
                new_conf = min(1.0, confidence) if not contradiction else confidence * 0.8
                await db.execute(
                    """UPDATE knowledge_facts SET object_value = ?, confidence = ?,
                       updated_at = ?, freshness_score = 1.0, status = ?
                       WHERE id = ?""",
                    (
                        object_value,
                        new_conf,
                        now,
                        "contradicted" if contradiction else "active",
                        fact_id,
                    ),
                )
                result = {
                    "factId": fact_id,
                    "status": "evolved" if not contradiction else "contradicted",
                    "previousValue": old_value,
                    "newValue": object_value,
                }
            else:
                await db.execute(
                    """UPDATE knowledge_facts SET confidence = MAX(confidence, ?),
                       updated_at = ?, freshness_score = 1.0 WHERE id = ?""",
                    (confidence, now, fact_id),
                )
                result = {"factId": fact_id, "status": "reinforced"}
        else:
            if not MemorySafety.world_model_confidence_ok(confidence):
                confidence = settings.world_model_confidence_threshold
            fact_id = f"fact-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO knowledge_facts
                   (id, subject, predicate, object_value, confidence, source_execution_id,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (fact_id, subject, predicate, object_value, confidence, source_execution_id, now, now),
            )
            ver_id = f"fv-{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO fact_versions
                   (id, fact_id, version, object_value, confidence, change_reason, created_at)
                   VALUES (?, ?, 1, ?, ?, 'initial', ?)""",
                (ver_id, fact_id, object_value, confidence, now),
            )
            result = {"factId": fact_id, "status": "created"}

        await db.commit()

        if emit_fn:
            await emit_fn(
                source_execution_id,
                "world_model_updated",
                "intelligence",
                f"Fact {result['status']}: {subject}",
                factId=result.get("factId"),
                status=result["status"],
            )
        return result

    @classmethod
    def _detect_contradiction(cls, old: str, new: str) -> bool:
        negations = ("not ", "no ", "never ", "false")
        old_l, new_l = old.lower(), new.lower()
        if any(n in old_l for n in negations) != any(n in new_l for n in negations):
            return True
        if old_l != new_l and len(set(old_l.split()) & set(new_l.split())) >= 2:
            return True
        return False

    @classmethod
    async def get_facts_for_subject(cls, subject: str) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, subject, predicate, object_value, confidence, status, updated_at
               FROM knowledge_facts WHERE lower(subject) LIKE lower(?)
               ORDER BY confidence DESC LIMIT 20""",
            (f"%{subject}%",),
        )
        return [
            {
                "id": r["id"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "objectValue": r["object_value"],
                "confidence": r["confidence"],
                "status": r["status"],
                "updatedAt": r["updated_at"],
            }
            for r in await cursor.fetchall()
        ]

    @classmethod
    async def get_timeline(cls, fact_id: str) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT version, object_value, confidence, change_reason, created_at
               FROM fact_versions WHERE fact_id = ?
               ORDER BY version ASC""",
            (fact_id,),
        )
        return [
            {
                "version": r["version"],
                "objectValue": r["object_value"],
                "confidence": r["confidence"],
                "changeReason": r["change_reason"],
                "createdAt": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]
