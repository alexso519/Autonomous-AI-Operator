"""
Self-improving runtime — analyze history and recommend bounded improvements.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.cognition.benchmark_analyzer import BenchmarkAnalyzer
from app.cognition.strategy_optimizer import StrategyOptimizer
from app.database.database import get_db

logger = logging.getLogger(__name__)


class SelfImprovementEngine:
    """
    Bounded self-improvement — recommendations only, no recursive code mutation.
    """

    @classmethod
    async def run_improvement_cycle(
        cls,
        execution_id: str,
        status: str,
        objective: str,
        ctx_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        benchmark = await BenchmarkAnalyzer.analyze_recent(days=30)
        strategy_result = await StrategyOptimizer.optimize_from_execution(
            execution_id, status, ctx_snapshot, benchmark
        )

        recommendations = cls._build_architecture_recommendations(
            benchmark, strategy_result, status
        )

        cycle = {
            "id": f"imp-{uuid.uuid4().hex[:12]}",
            "executionId": execution_id,
            "status": status,
            "benchmarkSummary": benchmark.get("summary", {}),
            "bottlenecks": benchmark.get("bottlenecks", [])[:5],
            "strategyOptimizations": strategy_result.get("optimizations", []),
            "architectureRecommendations": recommendations,
            "compositeExpectedGain": strategy_result.get("compositeExpectedGain", 0),
            "completedAt": datetime.now(timezone.utc).isoformat(),
        }

        await cls._persist_cycle(cycle)
        return cycle

    @classmethod
    def _build_architecture_recommendations(
        cls,
        benchmark: dict[str, Any],
        strategy: dict[str, Any],
        status: str,
    ) -> list[dict[str, Any]]:
        recs: list[dict[str, Any]] = []

        if benchmark.get("status") == "ok":
            summary = benchmark.get("summary", {})
            if summary.get("overallSuccessRate", 1) < 0.75:
                recs.append(
                    {
                        "type": "benchmark_regression",
                        "message": "Benchmark success below 75% — review deliberation depth and tool grounding",
                        "safe": True,
                    }
                )

        for opt in strategy.get("optimizations", []):
            if opt.get("target") == "architecture":
                recs.append(
                    {
                        "type": "runtime_tuning",
                        "message": opt.get("detail", ""),
                        "safe": True,
                    }
                )

        if status == "failed":
            recs.append(
                {
                    "type": "execution_recovery",
                    "message": "Store recovery strategy and increase debate rounds for similar objectives",
                    "safe": True,
                }
            )

        return recs[:10]

    @classmethod
    async def _persist_cycle(cls, cycle: dict[str, Any]) -> None:
        db = await get_db()
        await db.execute(
            """CREATE TABLE IF NOT EXISTS self_improvement_cycles (
                id              TEXT PRIMARY KEY,
                execution_id    TEXT NOT NULL,
                cycle_data      TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )"""
        )
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO self_improvement_cycles (id, execution_id, cycle_data, created_at)
               VALUES (?, ?, ?, ?)""",
            (cycle["id"], cycle["executionId"], json.dumps(cycle), now),
        )
        await db.commit()

    @classmethod
    async def get_recent_cycles(cls, limit: int = 10) -> list[dict[str, Any]]:
        db = await get_db()
        try:
            cursor = await db.execute(
                """SELECT cycle_data FROM self_improvement_cycles
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [json.loads(r["cycle_data"]) for r in rows]
        except Exception:
            return []
