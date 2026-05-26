"""
Embedding service — local Ollama embeddings with deterministic fallback.

Persistent embedding cache backed by SQLite.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384


def _hash_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic pseudo-embedding for offline / fallback use."""
    tokens = text.lower().split()
    vec = [0.0] * dim
    for token in tokens:
        h = int(hashlib.sha256(token.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EmbeddingService:
    """Generate and cache text embeddings."""

    @classmethod
    def _content_hash(cls, text: str, model: str) -> str:
        raw = f"{model}:{text[:8000]}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash   TEXT PRIMARY KEY,
                model          TEXT NOT NULL,
                embedding_blob TEXT NOT NULL,
                token_count    INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL
            )"""
        )
        await db.execute(
            """CREATE INDEX IF NOT EXISTS idx_embedding_cache_model
               ON embedding_cache(model)"""
        )
        await db.commit()

    @classmethod
    async def get_cached(cls, content_hash: str) -> list[float] | None:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            "SELECT embedding_blob FROM embedding_cache WHERE content_hash = ?",
            (content_hash,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        try:
            return json.loads(row["embedding_blob"])
        except json.JSONDecodeError:
            return None

    @classmethod
    async def store_cache(
        cls,
        content_hash: str,
        model: str,
        embedding: list[float],
        token_count: int = 0,
    ) -> None:
        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            """INSERT OR REPLACE INTO embedding_cache
               (content_hash, model, embedding_blob, token_count, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (content_hash, model, json.dumps(embedding), token_count, now),
        )
        await db.commit()

    @classmethod
    async def embed_ollama(cls, text: str, model: str) -> list[float] | None:
        if not settings.enable_vector_memory:
            return None
        url = f"{settings.ollama_url.rstrip('/')}/api/embeddings"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    json={"model": model, "prompt": text[:8000]},
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                emb = data.get("embedding")
                if isinstance(emb, list) and emb:
                    return [float(x) for x in emb]
        except Exception as exc:
            logger.debug("Ollama embedding failed: %s", exc)
        return None

    @classmethod
    async def embed(cls, text: str, *, use_cache: bool = True) -> list[float]:
        if not text.strip():
            return _hash_embedding("empty")

        model = settings.embedding_model
        content_hash = cls._content_hash(text, model)

        if use_cache:
            cached = await cls.get_cached(content_hash)
            if cached:
                return cached

        embedding: list[float] | None = None
        if settings.enable_vector_memory:
            embedding = await cls.embed_ollama(text, model)

        if embedding is None:
            embedding = _hash_embedding(text)

        if use_cache:
            await cls.store_cache(
                content_hash,
                model,
                embedding,
                token_count=len(text.split()),
            )
        return embedding

    @classmethod
    async def embed_batch(cls, texts: list[str]) -> list[list[float]]:
        return [await cls.embed(t) for t in texts]

    @classmethod
    async def deduplicate_check(cls, text: str, threshold: float = 0.98) -> str | None:
        """Return existing content_hash if near-duplicate embedding exists."""
        from app.intelligence.memory_safety import MemorySafety

        if not MemorySafety.should_deduplicate():
            return None

        emb = await cls.embed(text, use_cache=True)
        db = await get_db()
        await cls.ensure_tables()
        cursor = await db.execute(
            "SELECT content_hash, embedding_blob FROM embedding_cache ORDER BY created_at DESC LIMIT 200"
        )
        rows = await cursor.fetchall()
        for row in rows:
            try:
                existing = json.loads(row["embedding_blob"])
                if cosine_similarity(emb, existing) >= threshold:
                    return row["content_hash"]
            except json.JSONDecodeError:
                continue
        return None
