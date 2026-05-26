"""
Autonomous heuristic evolution — pattern mining, strategy extraction, failed-pattern suppression.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.strategy_mutator import StrategyMutator

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class HeuristicEvolution:
    @classmethod
    async def evolve_from_execution(
        cls,
        execution_id: str,
        status: str,
        objective: str,
        ctx_snapshot: dict[str, Any],
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        patterns = cls._mine_patterns(ctx_snapshot, status)
        heuristics = cls._extract_successful_strategies(patterns, status)
        generation = await cls._persist_generation(execution_id, heuristics, patterns)

        mutations = await StrategyMutator.mutate_and_rank(
            {"id": generation["id"], "weight": heuristics.get("orchestrationWeight", 0.5)},
            {
                "status": status,
                "retryCount": ctx_snapshot.get("global_retry_count", 0),
                "usedTools": bool(ctx_snapshot.get("tool_calls")),
            },
        )

        failure_cluster_id = None
        if status == "failed":
            failure_cluster_id = await cls._suppress_failure_pattern(objective, ctx_snapshot)
            if emit_fn and failure_cluster_id:
                await emit_fn(
                    execution_id,
                    "failure_pattern_detected",
                    "self_improvement",
                    "Failure cluster recorded and suppressed",
                    clusterId=failure_cluster_id,
                )

        if emit_fn:
            await emit_fn(
                execution_id,
                "heuristic_evolved",
                "self_improvement",
                f"Heuristic generation {generation['generation']} recorded",
                generation=generation,
                topMutation=mutations[0] if mutations else None,
            )
            if mutations:
                await emit_fn(
                    execution_id,
                    "strategy_mutated",
                    "self_improvement",
                    f"Ranked {len(mutations)} strategy mutation(s)",
                    mutations=mutations[:5],
                )

        return {
            "generation": generation,
            "patterns": patterns,
            "heuristics": heuristics,
            "mutations": mutations[:5],
        }

    @classmethod
    def _mine_patterns(cls, ctx: dict[str, Any], status: str) -> dict[str, Any]:
        reflections = ctx.get("reflection_history") or []
        tool_calls = ctx.get("tool_calls") or []
        return {
            "reflectionCount": len(reflections),
            "toolCallCount": len(tool_calls),
            "retryCount": int(ctx.get("global_retry_count") or 0),
            "status": status,
            "hasDeliberation": bool(ctx.get("cognitive_deliberation")),
        }

    @classmethod
    def _extract_successful_strategies(
        cls, patterns: dict[str, Any], status: str
    ) -> dict[str, Any]:
        weight = 0.55 if status == "completed" else 0.45
        if patterns.get("retryCount", 0) > 3:
            weight -= 0.08
        if patterns.get("toolCallCount", 0) > 0 and status == "completed":
            weight += 0.1
        return {
            "orchestrationWeight": min(1.0, max(0.2, weight)),
            "preferToolGrounding": patterns.get("toolCallCount", 0) > 0,
            "retryReductionBias": patterns.get("retryCount", 0) > 2,
            "confidenceWeighted": True,
        }

    @classmethod
    async def _persist_generation(
        cls,
        execution_id: str,
        heuristics: dict[str, Any],
        patterns: dict[str, Any],
    ) -> dict[str, Any]:
        gen_id = f"hg-{uuid.uuid4().hex[:12]}"
        rows = await ImprovementMemoryStore.query_recent("heuristic_generations", limit=1)
        gen_num = (int(rows[0]["generation"]) + 1) if rows else 1
        now = datetime.now(timezone.utc).isoformat()
        data = {"heuristics": heuristics, "patterns": patterns}
        await ImprovementMemoryStore.insert_row(
            "heuristic_generations",
            {
                "id": gen_id,
                "generation": gen_num,
                "heuristic_data": json.dumps(data),
                "score": heuristics.get("orchestrationWeight", 0.5),
                "source_execution_id": execution_id,
                "created_at": now,
            },
        )
        return {"id": gen_id, "generation": gen_num, **data}

    @classmethod
    async def _suppress_failure_pattern(cls, objective: str, ctx: dict[str, Any]) -> str:
        cluster_key = objective[:80].lower().strip() or "unknown"
        cluster_id = f"fc-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        reflections = ctx.get("reflection_history") or []
        failure_type = "execution_failed"
        if reflections:
            issues = (reflections[-1].get("issues") or [])
            if issues:
                failure_type = str(issues[0])

        await ImprovementMemoryStore.ensure_tables()
        from app.database.database import get_db

        db_conn = await get_db()
        cursor = await db_conn.execute(
            "SELECT id, occurrence_count FROM failure_clusters WHERE cluster_key = ?",
            (cluster_key,),
        )
        row = await cursor.fetchone()
        if row:
            await db_conn.execute(
                """UPDATE failure_clusters
                   SET occurrence_count = occurrence_count + 1,
                       pattern_data = ?, updated_at = ?, suppressed = 1
                   WHERE cluster_key = ?""",
                (
                    json.dumps({"failureType": failure_type}),
                    now,
                    cluster_key,
                ),
            )
            cluster_id = row["id"]
        else:
            await ImprovementMemoryStore.insert_row(
                "failure_clusters",
                {
                    "id": cluster_id,
                    "cluster_key": cluster_key,
                    "failure_type": failure_type,
                    "pattern_data": json.dumps({"objective": objective[:200]}),
                    "occurrence_count": 1,
                    "suppressed": 1,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        await db_conn.commit()
        return cluster_id

    @classmethod
    async def get_lineage(cls, limit: int = 30) -> list[dict[str, Any]]:
        gens = await ImprovementMemoryStore.query_recent("heuristic_generations", limit=limit)
        muts = await ImprovementMemoryStore.query_recent("strategy_mutations", limit=limit)
        return [{"generations": gens, "mutations": muts}]
