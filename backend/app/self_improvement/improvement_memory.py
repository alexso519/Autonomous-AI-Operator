"""
SQLite persistence for self-improvement artifacts (no vector DB required).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)

_TABLES = [
    """CREATE TABLE IF NOT EXISTS heuristic_generations (
        id              TEXT PRIMARY KEY,
        generation      INTEGER NOT NULL DEFAULT 1,
        heuristic_data  TEXT NOT NULL DEFAULT '{}',
        score           REAL NOT NULL DEFAULT 0.5,
        source_execution_id TEXT DEFAULT '',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS strategy_mutations (
        id              TEXT PRIMARY KEY,
        parent_id       TEXT DEFAULT '',
        mutation_type   TEXT NOT NULL DEFAULT 'weight_adjust',
        mutation_data   TEXT NOT NULL DEFAULT '{}',
        fitness_score   REAL NOT NULL DEFAULT 0.5,
        lineage         TEXT NOT NULL DEFAULT '[]',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS runtime_optimizations (
        id              TEXT PRIMARY KEY,
        optimization_type TEXT NOT NULL DEFAULT 'bottleneck',
        target          TEXT NOT NULL DEFAULT '',
        before_value    TEXT NOT NULL DEFAULT '{}',
        after_value     TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        applied         INTEGER NOT NULL DEFAULT 0,
        source_execution_id TEXT DEFAULT '',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS improvement_memory (
        id              TEXT PRIMARY KEY,
        memory_key      TEXT NOT NULL,
        memory_type     TEXT NOT NULL DEFAULT 'pattern',
        memory_data     TEXT NOT NULL DEFAULT '{}',
        confidence      REAL NOT NULL DEFAULT 0.5,
        use_count       INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS failure_clusters (
        id              TEXT PRIMARY KEY,
        cluster_key     TEXT NOT NULL,
        failure_type    TEXT NOT NULL DEFAULT '',
        pattern_data    TEXT NOT NULL DEFAULT '{}',
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        suppressed      INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS prompt_variants (
        id              TEXT PRIMARY KEY,
        role            TEXT NOT NULL DEFAULT 'agent',
        template_key    TEXT NOT NULL DEFAULT '',
        template_body   TEXT NOT NULL DEFAULT '',
        effectiveness   REAL NOT NULL DEFAULT 0.5,
        token_efficiency REAL NOT NULL DEFAULT 0.5,
        metadata        TEXT NOT NULL DEFAULT '{}',
        active          INTEGER NOT NULL DEFAULT 0,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS planning_profiles (
        id              TEXT PRIMARY KEY,
        profile_key     TEXT NOT NULL,
        depth           INTEGER NOT NULL DEFAULT 3,
        reflection_threshold REAL NOT NULL DEFAULT 0.5,
        context_allocation REAL NOT NULL DEFAULT 0.5,
        profile_data    TEXT NOT NULL DEFAULT '{}',
        effectiveness   REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS retry_profiles (
        id              TEXT PRIMARY KEY,
        profile_key     TEXT NOT NULL,
        max_retries     INTEGER NOT NULL DEFAULT 3,
        backoff_factor  REAL NOT NULL DEFAULT 1.5,
        profile_data    TEXT NOT NULL DEFAULT '{}',
        effectiveness   REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tool_selection_profiles (
        id              TEXT PRIMARY KEY,
        profile_key     TEXT NOT NULL,
        preferred_tools TEXT NOT NULL DEFAULT '[]',
        avoided_tools   TEXT NOT NULL DEFAULT '[]',
        profile_data    TEXT NOT NULL DEFAULT '{}',
        effectiveness   REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS benchmark_evolution (
        id              TEXT PRIMARY KEY,
        suite_id        TEXT DEFAULT '',
        evolution_data  TEXT NOT NULL DEFAULT '{}',
        trend_score     REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS regression_events (
        id              TEXT PRIMARY KEY,
        metric          TEXT NOT NULL DEFAULT 'success_rate',
        before_value    REAL NOT NULL DEFAULT 0,
        after_value     REAL NOT NULL DEFAULT 0,
        severity        TEXT NOT NULL DEFAULT 'warning',
        event_data      TEXT NOT NULL DEFAULT '{}',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS capability_trends (
        id              TEXT PRIMARY KEY,
        capability      TEXT NOT NULL,
        trend_data      TEXT NOT NULL DEFAULT '{}',
        score           REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS performance_evolution (
        id              TEXT PRIMARY KEY,
        metric          TEXT NOT NULL DEFAULT 'latency',
        evolution_data  TEXT NOT NULL DEFAULT '{}',
        score           REAL NOT NULL DEFAULT 0.5,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS improvement_sessions (
        id              TEXT PRIMARY KEY,
        execution_id    TEXT NOT NULL DEFAULT '',
        session_data    TEXT NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'completed',
        dry_run         INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL,
        completed_at    TEXT DEFAULT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS evolution_policies (
        id              TEXT PRIMARY KEY,
        policy_data     TEXT NOT NULL DEFAULT '{}',
        reason          TEXT DEFAULT '',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS optimization_rollbacks (
        id              TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL DEFAULT '',
        rollback_data   TEXT NOT NULL DEFAULT '{}',
        trigger_reason  TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS improvement_audits (
        id              TEXT PRIMARY KEY,
        session_id      TEXT NOT NULL DEFAULT '',
        action_type     TEXT NOT NULL DEFAULT '',
        audit_data      TEXT NOT NULL DEFAULT '{}',
        replayable      INTEGER NOT NULL DEFAULT 1,
        created_at      TEXT NOT NULL
    )""",
]


class ImprovementMemoryStore:
    _tables_ready = False

    @classmethod
    async def ensure_tables(cls) -> None:
        if cls._tables_ready:
            return
        db = await get_db()
        for sql in _TABLES:
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
    async def upsert_memory(
        cls,
        memory_id: str,
        memory_key: str,
        memory_type: str,
        memory_data: dict[str, Any],
        confidence: float,
    ) -> None:
        await cls.ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, use_count FROM improvement_memory WHERE memory_key = ?",
            (memory_key,),
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                """UPDATE improvement_memory
                   SET memory_data = ?, confidence = ?, use_count = use_count + 1, updated_at = ?
                   WHERE memory_key = ?""",
                (json.dumps(memory_data), confidence, now, memory_key),
            )
        else:
            await db.execute(
                """INSERT INTO improvement_memory
                   (id, memory_key, memory_type, memory_data, confidence, use_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (memory_id, memory_key, memory_type, json.dumps(memory_data), confidence, now, now),
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
