"""
Skill accumulator — agent and tool effectiveness profiling.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)


class SkillAccumulator:
    """Track agent skills and tool effectiveness across executions."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS skill_profiles (
                id              TEXT PRIMARY KEY,
                profile_type    TEXT NOT NULL,
                profile_key     TEXT NOT NULL,
                success_count   INTEGER NOT NULL DEFAULT 0,
                failure_count   INTEGER NOT NULL DEFAULT 0,
                avg_quality     REAL NOT NULL DEFAULT 0.5,
                avg_duration    REAL NOT NULL DEFAULT 0,
                effectiveness   REAL NOT NULL DEFAULT 0.5,
                metadata        TEXT NOT NULL DEFAULT '{}',
                updated_at      TEXT NOT NULL,
                UNIQUE(profile_type, profile_key)
            )"""
        )
        await db.commit()

    @classmethod
    async def record_agent_outcome(
        cls,
        agent_name: str,
        *,
        success: bool,
        quality: float = 0.5,
        duration: float = 0,
    ) -> None:
        await cls._record("agent", agent_name, success=success, quality=quality, duration=duration)

    @classmethod
    async def record_tool_outcome(
        cls,
        tool_name: str,
        *,
        success: bool,
        quality: float = 0.5,
        duration: float = 0,
    ) -> None:
        await cls._record("tool", tool_name, success=success, quality=quality, duration=duration)

    @classmethod
    async def _record(
        cls,
        profile_type: str,
        profile_key: str,
        *,
        success: bool,
        quality: float,
        duration: float,
    ) -> None:
        if not settings.enable_vector_memory:
            return

        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()

        cursor = await db.execute(
            """SELECT id, success_count, failure_count, avg_quality, avg_duration, effectiveness
               FROM skill_profiles WHERE profile_type = ? AND profile_key = ?""",
            (profile_type, profile_key),
        )
        row = await cursor.fetchone()

        if row:
            sc = row["success_count"] + (1 if success else 0)
            fc = row["failure_count"] + (0 if success else 1)
            total = sc + fc
            new_q = (row["avg_quality"] * (total - 1) + quality) / total
            new_d = (row["avg_duration"] * (total - 1) + duration) / total
            eff = (sc / max(total, 1)) * 0.4 + new_q * 0.6
            await db.execute(
                """UPDATE skill_profiles SET success_count = ?, failure_count = ?,
                   avg_quality = ?, avg_duration = ?, effectiveness = ?, updated_at = ?
                   WHERE id = ?""",
                (sc, fc, new_q, new_d, eff, now, row["id"]),
            )
        else:
            pid = f"sp-{uuid.uuid4().hex[:12]}"
            eff = (0.5 if success else 0.3) * 0.4 + quality * 0.6
            await db.execute(
                """INSERT INTO skill_profiles
                   (id, profile_type, profile_key, success_count, failure_count,
                    avg_quality, avg_duration, effectiveness, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pid,
                    profile_type,
                    profile_key,
                    1 if success else 0,
                    0 if success else 1,
                    quality,
                    duration,
                    eff,
                    now,
                ),
            )
        await db.commit()

    @classmethod
    async def get_top_skills(cls, profile_type: str = "agent", limit: int = 10) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        cursor = await db.execute(
            """SELECT profile_key, success_count, failure_count, effectiveness, avg_quality
               FROM skill_profiles WHERE profile_type = ?
               ORDER BY effectiveness DESC LIMIT ?""",
            (profile_type, limit),
        )
        return [
            {
                "key": r["profile_key"],
                "successCount": r["success_count"],
                "failureCount": r["failure_count"],
                "effectiveness": round(r["effectiveness"], 3),
                "avgQuality": round(r["avg_quality"], 3),
            }
            for r in await cursor.fetchall()
        ]
