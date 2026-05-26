"""
Domain abstraction — domain pattern extraction from execution history.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)

DOMAIN_KEYWORDS = {
    "research": ("research", "investigate", "analyze", "report"),
    "coding": ("code", "fix", "test", "repository", "patch"),
    "planning": ("plan", "milestone", "phase", "strategy"),
    "browser": ("browser", "navigate", "screenshot", "web page"),
    "intelligence": ("memory", "recall", "semantic", "knowledge"),
}


class DomainAbstraction:
    """Extract domain patterns from objectives and executions."""

    @classmethod
    async def ensure_tables(cls) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS domain_patterns (
                id              TEXT PRIMARY KEY,
                domain          TEXT NOT NULL,
                pattern_label   TEXT NOT NULL,
                pattern_json    TEXT NOT NULL DEFAULT '{}',
                occurrence_count INTEGER NOT NULL DEFAULT 1,
                success_rate    REAL NOT NULL DEFAULT 0.5,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )"""
        )
        await db.commit()

    @classmethod
    def classify_domain(cls, text: str) -> str:
        lower = text.lower()
        scores = {
            domain: sum(1 for kw in kws if kw in lower)
            for domain, kws in DOMAIN_KEYWORDS.items()
        }
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "general"

    @classmethod
    async def record_pattern(
        cls,
        objective: str,
        *,
        success: bool = True,
        execution_id: str = "",
    ) -> dict[str, Any] | None:
        if not settings.enable_vector_memory:
            return None

        await cls.ensure_tables()
        domain = cls.classify_domain(objective)
        tokens = [t.lower() for t in re.findall(r"\w{4,}", objective)][:8]
        label = f"{domain}:{tokens[0] if tokens else 'generic'}"

        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        cursor = await db.execute(
            "SELECT id, occurrence_count, success_rate, pattern_json FROM domain_patterns WHERE pattern_label = ?",
            (label,),
        )
        row = await cursor.fetchone()

        if row:
            count = int(row["occurrence_count"]) + 1
            rate = float(row["success_rate"])
            new_rate = (rate * (count - 1) + (1.0 if success else 0.0)) / count
            pattern = json.loads(row["pattern_json"] or "{}")
            token_counts: Counter = Counter(pattern.get("tokens", {}))
            token_counts.update(tokens)
            pattern["tokens"] = dict(token_counts.most_common(12))
            await db.execute(
                """UPDATE domain_patterns SET occurrence_count = ?, success_rate = ?,
                   pattern_json = ?, updated_at = ? WHERE id = ?""",
                (count, new_rate, json.dumps(pattern), now, row["id"]),
            )
            pid = row["id"]
        else:
            pid = f"dp-{uuid.uuid4().hex[:12]}"
            pattern = {"tokens": dict(Counter(tokens)), "executionId": execution_id}
            await db.execute(
                """INSERT INTO domain_patterns
                   (id, domain, pattern_label, pattern_json, occurrence_count,
                    success_rate, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (pid, domain, label, json.dumps(pattern), 1.0 if success else 0.0, now, now),
            )

        await db.commit()
        return {"id": pid, "domain": domain, "label": label}

    @classmethod
    async def get_patterns(cls, domain: str = "", limit: int = 20) -> list[dict[str, Any]]:
        await cls.ensure_tables()
        db = await get_db()
        if domain:
            cursor = await db.execute(
                """SELECT id, domain, pattern_label, occurrence_count, success_rate, updated_at
                   FROM domain_patterns WHERE domain = ? ORDER BY occurrence_count DESC LIMIT ?""",
                (domain, limit),
            )
        else:
            cursor = await db.execute(
                """SELECT id, domain, pattern_label, occurrence_count, success_rate, updated_at
                   FROM domain_patterns ORDER BY success_rate DESC LIMIT ?""",
                (limit,),
            )
        return [
            {
                "id": r["id"],
                "domain": r["domain"],
                "label": r["pattern_label"],
                "occurrenceCount": r["occurrence_count"],
                "successRate": r["success_rate"],
                "updatedAt": r["updated_at"],
            }
            for r in await cursor.fetchall()
        ]
