"""
Tool efficiency layer — duplicate detection, caching, cooldowns.

Cache persisted in SQLite with deterministic keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)

DEFAULT_COOLDOWN_SECONDS = 30
CACHE_TTL_HOURS = 24


@dataclass
class CacheLookupResult:
    hit: bool
    output: dict[str, Any] | None
    cache_key: str
    reason: str = ""


@dataclass
class EfficiencyDecision:
    allow: bool
    cache_hit: bool = False
    reused_result: bool = False
    suppressed: bool = False
    cache_key: str = ""
    message: str = ""


class ToolEfficiencyLayer:
    """Prevent redundant tool calls and serve cached results."""

    _recent_calls: dict[str, datetime] = {}

    @classmethod
    def cache_key(cls, tool_name: str, input_data: dict[str, Any]) -> str:
        """Deterministic cache key from tool + normalized input."""
        normalized = json.dumps(input_data, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(
            f"{tool_name}:{normalized}".encode("utf-8")
        ).hexdigest()[:32]
        return digest

    @classmethod
    async def lookup_cache(
        cls, tool_name: str, input_data: dict[str, Any]
    ) -> CacheLookupResult:
        key = cls.cache_key(tool_name, input_data)
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT output_data, created_at FROM tool_result_cache
                   WHERE cache_key = ? AND tool_name = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (key, tool_name),
            )
            row = await cursor.fetchone()
            if not row:
                return CacheLookupResult(hit=False, output=None, cache_key=key)

            created = datetime.fromisoformat(
                str(row["created_at"]).replace("Z", "+00:00")
            )
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - created
            if age > timedelta(hours=CACHE_TTL_HOURS):
                return CacheLookupResult(
                    hit=False, output=None, cache_key=key, reason="expired"
                )

            output = json.loads(row["output_data"] or "{}")
            return CacheLookupResult(hit=True, output=output, cache_key=key)
        except Exception as exc:
            logger.warning("Cache lookup failed: %s", exc)
            return CacheLookupResult(hit=False, output=None, cache_key=key)

    @classmethod
    async def store_cache(
        cls,
        tool_name: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        execution_id: str | None = None,
    ) -> str:
        key = cls.cache_key(tool_name, input_data)
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT OR REPLACE INTO tool_result_cache
                   (cache_key, tool_name, input_hash, output_data, execution_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    key,
                    tool_name,
                    key,
                    json.dumps(output_data, ensure_ascii=False),
                    execution_id or "",
                    now,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Cache store failed: %s", exc)
        return key

    @classmethod
    def is_duplicate_inflight(cls, tool_name: str, input_data: dict[str, Any]) -> bool:
        """Detect rapid repeated identical calls (in-process cooldown)."""
        key = cls.cache_key(tool_name, input_data)
        last = cls._recent_calls.get(key)
        now = datetime.now(timezone.utc)
        if last and (now - last).total_seconds() < DEFAULT_COOLDOWN_SECONDS:
            return True
        cls._recent_calls[key] = now
        return False

    @classmethod
    async def check_before_execute(
        cls,
        tool_name: str,
        input_data: dict[str, Any],
        execution_id: str | None = None,
    ) -> EfficiencyDecision:
        """Pre-execution check: cache hit, duplicate suppression."""
        if cls.is_duplicate_inflight(tool_name, input_data):
            cached = await cls.lookup_cache(tool_name, input_data)
            if cached.hit and cached.output:
                return EfficiencyDecision(
                    allow=False,
                    cache_hit=True,
                    reused_result=True,
                    cache_key=cached.cache_key,
                    message="repeated_query_suppressed_using_cache",
                )
            return EfficiencyDecision(
                allow=False,
                suppressed=True,
                cache_key=cached.cache_key,
                message="cooldown_active_duplicate_suppressed",
            )

        cached = await cls.lookup_cache(tool_name, input_data)
        if cached.hit and cached.output:
            if execution_id:
                await cls.record_cache_event(
                    execution_id, tool_name, cached.cache_key, "cache_hit"
                )
            return EfficiencyDecision(
                allow=False,
                cache_hit=True,
                reused_result=True,
                cache_key=cached.cache_key,
                message="cache_hit",
            )

        return EfficiencyDecision(allow=True, cache_key=cached.cache_key)

    @classmethod
    async def record_cache_event(
        cls,
        execution_id: str,
        tool_name: str,
        cache_key: str,
        event_type: str,
    ) -> None:
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO tool_cache_events
                   (id, execution_id, tool_name, cache_key, event_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex[:12], execution_id, tool_name, cache_key, event_type, now),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Cache event persist failed: %s", exc)

    @classmethod
    async def prune_expired_cache(cls, max_entries: int = 5000) -> int:
        """Remove expired entries and enforce max cache size."""
        try:
            db = await get_db()
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)
            ).isoformat()
            cursor = await db.execute(
                "DELETE FROM tool_result_cache WHERE created_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount or 0

            cursor = await db.execute("SELECT COUNT(*) as c FROM tool_result_cache")
            row = await cursor.fetchone()
            count = row["c"] if row else 0
            if count > max_entries:
                overflow = count - max_entries
                await db.execute(
                    """DELETE FROM tool_result_cache WHERE cache_key IN (
                       SELECT cache_key FROM tool_result_cache
                       ORDER BY created_at ASC LIMIT ?)""",
                    (overflow,),
                )
                deleted += overflow
            await db.commit()
            return deleted
        except Exception as exc:
            logger.warning("Cache prune failed: %s", exc)
            return 0
