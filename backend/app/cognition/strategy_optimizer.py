"""
Optimize prompts, retry strategies, and tool usage from execution patterns.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db


class StrategyOptimizer:
    """Bounded strategy adjustments — no code self-modification."""

    @classmethod
    async def optimize_from_execution(
        cls,
        execution_id: str,
        status: str,
        ctx_snapshot: dict[str, Any],
        benchmark_insights: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        reflections = ctx_snapshot.get("reflection_history") or []
        tool_calls = ctx_snapshot.get("tool_calls") or []
        retry_count = int(ctx_snapshot.get("global_retry_count") or 0)
        deliberation = ctx_snapshot.get("cognitive_deliberation") or {}

        optimizations: list[dict[str, Any]] = []

        # Prompt hints
        if status == "failed" and retry_count >= 2:
            optimizations.append(
                {
                    "target": "prompts",
                    "change": "prepend_verification_instruction",
                    "detail": "Add explicit 'cite sources' and 'state uncertainty' to agent goals",
                    "expectedGain": 0.08,
                }
            )

        if len(reflections) > 3:
            issues = []
            for r in reflections:
                issues.extend(r.get("issues") or [])
            if "unsupported_claims" in issues or "hallucination_risk" in issues:
                optimizations.append(
                    {
                        "target": "prompts",
                        "change": "enforce_tool_grounding",
                        "detail": "Require web_search before factual claims",
                        "expectedGain": 0.12,
                    }
                )

        # Retry strategy
        if retry_count > 0 and status == "completed":
            optimizations.append(
                {
                    "target": "retry_strategy",
                    "change": "preserve_current_policy",
                    "detail": f"Retries ({retry_count}) led to success — keep RetryPolicy thresholds",
                    "expectedGain": 0.0,
                }
            )
        elif status == "failed":
            optimizations.append(
                {
                    "target": "retry_strategy",
                    "change": "increase_factual_retry_budget",
                    "detail": "Allow one extra factual_retry for research objectives",
                    "expectedGain": 0.1,
                }
            )

        # Tool usage
        tool_count = len(tool_calls) if isinstance(tool_calls, list) else 0
        if tool_count == 0 and status != "completed":
            optimizations.append(
                {
                    "target": "tool_usage",
                    "change": "mandate_initial_web_search",
                    "detail": "Planner should invoke web_search in first tool-eligible step",
                    "expectedGain": 0.15,
                }
            )
        elif tool_count > 8:
            optimizations.append(
                {
                    "target": "tool_usage",
                    "change": "enable_tool_deduplication",
                    "detail": "Strengthen cache reuse for repeated queries",
                    "expectedGain": 0.1,
                }
            )

        # Architecture from benchmark
        if benchmark_insights:
            for rec in benchmark_insights.get("recommendations", [])[:3]:
                if rec.get("area") == "architecture":
                    optimizations.append(
                        {
                            "target": "architecture",
                            "change": "adjust_agent_spawn",
                            "detail": rec.get("action", ""),
                            "expectedGain": 0.05,
                            "source": "benchmark",
                        }
                    )

        # Deliberation tuning
        uncertainty = (deliberation.get("uncertainty") or {}).get("overall", 0)
        if uncertainty > 0.5 and status == "completed":
            optimizations.append(
                {
                    "target": "cognition",
                    "change": "expand_debate_rounds",
                    "detail": "High uncertainty completed — debate path was effective",
                    "expectedGain": 0.05,
                }
            )

        composite_gain = sum(o.get("expectedGain", 0) for o in optimizations)
        result = {
            "executionId": execution_id,
            "status": status,
            "optimizations": optimizations,
            "compositeExpectedGain": round(min(0.35, composite_gain), 3),
            "optimizedAt": datetime.now(timezone.utc).isoformat(),
        }

        await cls._persist(execution_id, result)
        return result

    @classmethod
    async def _persist(cls, execution_id: str, result: dict[str, Any]) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS cognitive_optimizations (
                id              TEXT PRIMARY KEY,
                execution_id    TEXT NOT NULL,
                result_data     TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )"""
        )
        import uuid
        opt_id = f"opt-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO cognitive_optimizations (id, execution_id, result_data, created_at)
               VALUES (?, ?, ?, ?)""",
            (opt_id, execution_id, json.dumps(result), now),
        )
        await db.commit()

    @classmethod
    async def get_latest_for_execution(cls, execution_id: str) -> dict[str, Any] | None:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT result_data FROM cognitive_optimizations
                   WHERE execution_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (execution_id,),
            )
            row = await cursor.fetchone()
            if row:
                return json.loads(row["result_data"])
        except Exception:
            pass
        return None
