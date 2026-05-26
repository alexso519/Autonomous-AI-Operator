"""
Knowledge synthesizer — cross-source synthesis and summarization layers.
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
from app.intelligence.semantic_retriever import SemanticRetriever

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class KnowledgeSynthesizer:
    """Synthesize knowledge from multiple sources into persistent summaries."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS synthesized_knowledge (
                id              TEXT PRIMARY KEY,
                topic           TEXT NOT NULL,
                summary         TEXT NOT NULL,
                layer           TEXT NOT NULL DEFAULT 'summary',
                confidence      REAL NOT NULL DEFAULT 0.5,
                source_count    INTEGER NOT NULL DEFAULT 0,
                execution_id    TEXT DEFAULT '',
                created_at      TEXT NOT NULL
            )"""
        )
        await db.commit()

    @classmethod
    async def synthesize_from_sources(
        cls,
        topic: str,
        sources: list[str],
        *,
        execution_id: str = "",
        layer: str = "summary",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any] | None:
        if not settings.enable_vector_memory or not sources:
            return None

        filtered = [s for s in sources if MemorySafety.passes_privacy_filter(s)]
        if not filtered:
            return None

        await cls.ensure_tables()
        combined = " ".join(filtered)[:2000]
        summary = cls._extract_summary(combined, topic)
        confidence = min(1.0, 0.4 + 0.1 * len(filtered))

        if confidence < MemorySafety.min_inference_confidence():
            return None

        now = datetime.now(timezone.utc).isoformat()
        sid = f"syn-{uuid.uuid4().hex[:12]}"
        db = await get_db()
        await db.execute(
            """INSERT INTO synthesized_knowledge
               (id, topic, summary, layer, confidence, source_count, execution_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, topic[:120], summary[:1500], layer, confidence, len(filtered), execution_id, now),
        )
        await db.commit()

        if emit_fn:
            await emit_fn(
                execution_id,
                "synthesized_knowledge_created",
                "intelligence",
                f"Synthesized knowledge: {topic[:50]}",
                synthesisId=sid,
                topic=topic,
                confidence=confidence,
                sourceCount=len(filtered),
            )

        return {
            "id": sid,
            "topic": topic,
            "summary": summary,
            "confidence": confidence,
            "sourceCount": len(filtered),
        }

    @classmethod
    def _extract_summary(cls, text: str, topic: str) -> str:
        sentences = [s.strip() for s in text.replace("\n", " ").split(".") if len(s.strip()) > 20]
        if not sentences:
            return f"Knowledge about {topic}."
        return ". ".join(sentences[:4]) + "."

    @classmethod
    async def synthesize_for_objective(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        memories = await SemanticRetriever.retrieve_for_objective(
            objective,
            execution_id=execution_id,
            limit=MemorySafety.retrieval_budget(),
            emit_fn=emit_fn,
        )
        sources = [str(m.get("content", m.get("text", ""))) for m in memories]
        result = await cls.synthesize_from_sources(
            objective[:120], sources,
            execution_id=execution_id, emit_fn=emit_fn,
        )
        return {"synthesis": result, "memoryCount": len(memories)}

    @classmethod
    async def get_synthesized(cls, limit: int = 20) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, topic, summary, layer, confidence, source_count, created_at
               FROM synthesized_knowledge ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [
            {
                "id": r["id"],
                "topic": r["topic"],
                "summary": r["summary"][:300],
                "layer": r["layer"],
                "confidence": r["confidence"],
                "sourceCount": r["source_count"],
                "createdAt": r["created_at"],
            }
            for r in await cursor.fetchall()
        ]
