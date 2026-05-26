"""
Benchmark-driven continuous improvement — replay guidance, strategy ranking.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class BenchmarkOptimizer:
    @classmethod
    async def run_optimization_pass(
        cls,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_benchmark_optimization:
            return {"skipped": True}

        from app.self_improvement.improvement_memory import ImprovementMemoryStore

        summary: dict[str, Any] = {"tasks": [], "successRate": 0.0}
        try:
            from app.benchmark.benchmark_runner import BenchmarkRunner

            history = await BenchmarkRunner.get_history(limit=20)
            if history:
                successes = sum(1 for h in history if h.get("success"))
                summary["successRate"] = round(100 * successes / len(history), 1)
                summary["tasks"] = [h.get("taskId") for h in history[:5]]
        except Exception as exc:
            logger.debug("Benchmark history unavailable: %s", exc)
            try:
                from app.cognition.benchmark_analyzer import BenchmarkAnalyzer

                analysis = await BenchmarkAnalyzer.analyze_recent(days=30)
                summary = analysis.get("summary", summary)
            except Exception as exc2:
                logger.debug("Benchmark analyzer fallback failed: %s", exc2)

        evo_id = f"be-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await ImprovementMemoryStore.insert_row(
            "benchmark_evolution",
            {
                "id": evo_id,
                "suite_id": "default",
                "evolution_data": json.dumps(summary),
                "trend_score": float(summary.get("overallSuccessRate", summary.get("successRate", 50))) / 100,
                "created_at": now,
            },
        )

        ranking = await cls._rank_strategies(summary)
        result = {"evolutionId": evo_id, "summary": summary, "strategyRanking": ranking}

        if emit_fn:
            await emit_fn(
                execution_id or "system",
                "benchmark_optimization_completed",
                "self_improvement",
                "Benchmark optimization pass completed",
                **result,
            )
        return result

    @classmethod
    async def _rank_strategies(cls, summary: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            from app.intelligence.strategy_evolution import StrategyEvolution

            strategies = await StrategyEvolution.get_evolution_history(limit=10)
            rate = float(summary.get("overallSuccessRate", summary.get("successRate", 50))) / 100
            return [
                {**s, "benchmarkScore": round(rate * float(s.get("compositeScore", 0.5)), 3)}
                for s in strategies
            ]
        except Exception:
            return []
