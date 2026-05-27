"""
SQLite persistence for recursive intelligence / Phase 2 evolution artifacts.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)

_PHASE2_TABLES = [
    """CREATE TABLE IF NOT EXISTS meta_reflections (
        id              TEXT PRIMARY KEY,
        execution_id    TEXT NOT NULL DEFAULT '',
        depth           INTEGER NOT NULL DEFAULT 0,
        reflection_data TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS capability_gaps (
        id              TEXT PRIMARY KEY,
        gap_key         TEXT NOT NULL,
        gap_data        TEXT NOT NULL DEFAULT '{}',
        severity        TEXT NOT NULL DEFAULT 'info',
        confidence      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS runtime_failure_patterns (
        id              TEXT PRIMARY KEY,
        pattern_key     TEXT NOT NULL,
        pattern_data    TEXT NOT NULL DEFAULT '{}',
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        cluster_id      TEXT DEFAULT '',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS reasoning_insights (
        id              TEXT PRIMARY KEY,
        insight_type    TEXT NOT NULL DEFAULT 'general',
        insight_data    TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        source_execution_id TEXT DEFAULT '',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS discovered_capabilities (
        id              TEXT PRIMARY KEY,
        capability_key  TEXT NOT NULL,
        capability_data TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        status          TEXT NOT NULL DEFAULT 'proposed',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS agent_archetype_proposals (
        id              TEXT PRIMARY KEY,
        archetype_name  TEXT NOT NULL,
        proposal_data   TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_gap_reports (
        id              TEXT PRIMARY KEY,
        tool_name       TEXT NOT NULL DEFAULT '',
        gap_data        TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS workflow_evolution_history (
        id              TEXT PRIMARY KEY,
        evolution_type  TEXT NOT NULL DEFAULT 'topology',
        evolution_data  TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS generated_scenarios (
        id              TEXT PRIMARY KEY,
        scenario_type   TEXT NOT NULL DEFAULT 'benchmark',
        scenario_data   TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS adversarial_runs (
        id              TEXT PRIMARY KEY,
        scenario_id     TEXT DEFAULT '',
        run_data        TEXT NOT NULL DEFAULT '{}',
        resilience_score REAL NOT NULL DEFAULT 0.5,
        status          TEXT NOT NULL DEFAULT 'completed',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS stress_profiles (
        id              TEXT PRIMARY KEY,
        profile_type    TEXT NOT NULL DEFAULT 'retry_storm',
        profile_data    TEXT NOT NULL DEFAULT '{}',
        severity        REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS resilience_scores (
        id              TEXT PRIMARY KEY,
        metric          TEXT NOT NULL DEFAULT 'overall',
        score           REAL NOT NULL DEFAULT 0.5,
        score_data      TEXT NOT NULL DEFAULT '{}',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS coordination_patterns (
        id              TEXT PRIMARY KEY,
        pattern_key     TEXT NOT NULL,
        pattern_data    TEXT NOT NULL DEFAULT '{}',
        rank_score      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS delegation_profiles (
        id              TEXT PRIMARY KEY,
        profile_key     TEXT NOT NULL,
        profile_data    TEXT NOT NULL DEFAULT '{}',
        effectiveness   REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS hierarchy_generations (
        id              TEXT PRIMARY KEY,
        generation      INTEGER NOT NULL DEFAULT 1,
        hierarchy_data  TEXT NOT NULL DEFAULT '{}',
        score           REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS collaboration_rankings (
        id              TEXT PRIMARY KEY,
        pattern_name    TEXT NOT NULL,
        ranking_data    TEXT NOT NULL DEFAULT '{}',
        rank_score      REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS runtime_predictions (
        id              TEXT PRIMARY KEY,
        prediction_type TEXT NOT NULL DEFAULT 'failure',
        prediction_data TEXT NOT NULL DEFAULT '{}',
        probability     REAL NOT NULL DEFAULT 0.5,
        execution_id    TEXT DEFAULT '',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS failure_forecasts (
        id              TEXT PRIMARY KEY,
        forecast_key    TEXT NOT NULL,
        forecast_data   TEXT NOT NULL DEFAULT '{}',
        probability     REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS recovery_plans (
        id              TEXT PRIMARY KEY,
        plan_type       TEXT NOT NULL DEFAULT 'proactive',
        plan_data       TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        applied         INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS predictive_adaptations (
        id              TEXT PRIMARY KEY,
        adaptation_type TEXT NOT NULL DEFAULT 'throttle',
        adaptation_data TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        applied         INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS evolution_governance_log (
        id              TEXT PRIMARY KEY,
        event_type      TEXT NOT NULL DEFAULT 'check',
        event_data      TEXT NOT NULL DEFAULT '{}',
        blocked           INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
    )""",
]


class MetaReasoningMemory:
    _tables_ready = False

    @classmethod
    async def ensure_tables(cls) -> None:
        if cls._tables_ready:
            return
        db = await get_db()
        for sql in _PHASE2_TABLES:
            await db.execute(sql)
        await db.commit()
        cls._tables_ready = True

    @classmethod
    async def insert_row(cls, table: str, row: dict[str, Any]) -> None:
        await cls.ensure_tables()
        cols = list(row.keys())
        placeholders = ", ".join("?" * len(cols))
        col_names = ", ".join(cols)
        db = await get_db()
        await db.execute(
            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})",
            tuple(row[c] for c in cols),
        )
        await db.commit()

    @classmethod
    async def query_recent(
        cls, table: str, order_col: str = "created_at", limit: int = 20
    ) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        try:
            cursor = await db.execute(
                f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    @classmethod
    async def count_today(cls, table: str) -> int:
        await cls.ensure_tables()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        db = await get_db()
        try:
            cursor = await db.execute(
                f"SELECT COUNT(*) as cnt FROM {table} WHERE created_at LIKE ?",
                (f"{today}%",),
            )
            row = await cursor.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            return 0

    @classmethod
    async def upsert_failure_pattern(
        cls,
        pattern_id: str,
        pattern_key: str,
        pattern_data: dict[str, Any],
        cluster_id: str = "",
    ) -> None:
        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, occurrence_count FROM runtime_failure_patterns WHERE pattern_key = ?",
            (pattern_key,),
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                """UPDATE runtime_failure_patterns
                   SET pattern_data = ?, occurrence_count = occurrence_count + 1,
                       cluster_id = ?, updated_at = ?
                   WHERE pattern_key = ?""",
                (json.dumps(pattern_data), cluster_id, now, pattern_key),
            )
        else:
            await db.execute(
                """INSERT INTO runtime_failure_patterns
                   (id, pattern_key, pattern_data, occurrence_count, cluster_id, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?, ?)""",
                (pattern_id, pattern_key, json.dumps(pattern_data), cluster_id, now, now),
            )
        await db.commit()

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._tables_ready = False
