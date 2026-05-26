"""
Analyze benchmark history to identify bottlenecks and improvement opportunities.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)


class BenchmarkAnalyzer:
    """Rule-based analysis of benchmark_runs — bounded recommendations."""

    @classmethod
    async def analyze_recent(cls, days: int = 30, limit: int = 200) -> dict[str, Any]:
        db = await get_db()
        cursor = await db.execute(
            """SELECT task_id, category, success, duration_seconds, metrics_data, created_at
               FROM benchmark_runs
               WHERE created_at > datetime('now', ?)
               ORDER BY created_at DESC
               LIMIT ?""",
            (f"-{days} days", limit),
        )
        rows = await cursor.fetchall()

        if not rows:
            return {
                "status": "no_data",
                "bottlenecks": [],
                "recommendations": [],
                "summary": {},
            }

        by_task: dict[str, list[dict[str, Any]]] = {}
        by_category: dict[str, list[bool]] = {}
        durations: list[float] = []

        for r in rows:
            tid = r["task_id"]
            by_task.setdefault(tid, []).append(
                {
                    "success": bool(r["success"]),
                    "duration": float(r["duration_seconds"]),
                    "metrics": json.loads(r["metrics_data"] or "{}"),
                }
            )
            by_category.setdefault(r["category"], []).append(bool(r["success"]))
            durations.append(float(r["duration_seconds"]))

        bottlenecks: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []

        for task_id, runs in by_task.items():
            success_rate = sum(1 for x in runs if x["success"]) / len(runs)
            avg_dur = sum(x["duration"] for x in runs) / len(runs)
            if success_rate < 0.7:
                bottlenecks.append(
                    {
                        "type": "low_success_rate",
                        "taskId": task_id,
                        "successRate": round(success_rate, 3),
                        "sampleSize": len(runs),
                    }
                )
                recommendations.append(
                    {
                        "area": "retry_strategy",
                        "taskId": task_id,
                        "action": "Increase factual retry and tool grounding for this task category",
                        "priority": "high",
                    }
                )
            if avg_dur > 120:
                bottlenecks.append(
                    {
                        "type": "slow_execution",
                        "taskId": task_id,
                        "avgDurationSeconds": round(avg_dur, 1),
                    }
                )
                recommendations.append(
                    {
                        "area": "tool_usage",
                        "taskId": task_id,
                        "action": "Enable parallel research sweep and cache warm paths",
                        "priority": "medium",
                    }
                )

        for cat, outcomes in by_category.items():
            rate = sum(outcomes) / len(outcomes)
            if rate < 0.65:
                recommendations.append(
                    {
                        "area": "architecture",
                        "category": cat,
                        "action": f"Review agent decomposition for '{cat}' benchmarks",
                        "priority": "medium",
                    }
                )

        avg_duration = sum(durations) / len(durations) if durations else 0
        overall_success = sum(1 for r in rows if r["success"]) / len(rows)

        intelligence_insights: dict[str, Any] = {}
        try:
            from app.intelligence.strategy_evolution import StrategyEvolution
            from app.intelligence.skill_accumulator import SkillAccumulator

            strategies = await StrategyEvolution.get_evolution_history(limit=5)
            agent_skills = await SkillAccumulator.get_top_skills("agent", limit=5)
            intelligence_insights = {
                "topLearnedStrategies": strategies,
                "topAgentSkills": agent_skills,
            }
            if strategies:
                recommendations.append(
                    {
                        "area": "strategy_reuse",
                        "action": f"Prefer '{strategies[0]['strategyLabel']}' for similar tasks",
                        "priority": "low",
                    }
                )
        except Exception:
            pass

        return {
            "status": "ok",
            "analyzedAt": datetime.now(timezone.utc).isoformat(),
            "sampleSize": len(rows),
            "summary": {
                "overallSuccessRate": round(overall_success, 3),
                "avgDurationSeconds": round(avg_duration, 1),
                "taskCount": len(by_task),
            },
            "bottlenecks": bottlenecks[:15],
            "recommendations": recommendations[:20],
            "intelligenceInsights": intelligence_insights,
        }
