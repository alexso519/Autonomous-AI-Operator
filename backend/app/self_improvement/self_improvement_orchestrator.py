"""
Central self-improvement lifecycle — bounded cycles, audit, rollback-safe evolution.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.execution.context_manager import ContextManager
from app.self_improvement.evolution_guardrails import EvolutionGuardrails
from app.self_improvement.evolution_policy import EvolutionPolicy
from app.self_improvement.improvement_coordinator import ImprovementCoordinator
from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.improvement_safety import ImprovementSafety

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class SelfImprovementOrchestrator:
    """
    Bounded self-optimizing runtime orchestrator.

    - Optional via settings.enable_self_improvement
    - Dry-run by default (self_improvement_dry_run)
    - No source-code modification
    - Inspectable sessions + audits
    """

    @classmethod
    def is_enabled(cls) -> bool:
        return settings.enable_self_improvement

    @classmethod
    async def run_post_execution_cycle(
        cls,
        execution_id: str,
        objective: str,
        status: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not cls.is_enabled():
            return {"skipped": True, "reason": "disabled"}

        policy = await EvolutionPolicy.get_active_policy()
        dry_run = bool(policy.get("dryRun", settings.self_improvement_dry_run))

        workflow = ctx.get_state().get("workflow", {})
        if not isinstance(workflow, dict):
            workflow = {}

        stable, reason = EvolutionGuardrails.check_runtime_stable(workflow)
        if not stable:
            logger.info("Self-improvement skipped: %s", reason)
            return {"skipped": True, "reason": reason}

        session_id = f"si-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        await emit_fn(
            execution_id,
            "improvement_cycle_started",
            "self_improvement",
            f"Self-improvement cycle started (dry_run={dry_run})",
            sessionId=session_id,
            dryRun=dry_run,
        )

        EvolutionGuardrails.mark_cycle_started()
        await ImprovementMemoryStore.ensure_tables()
        await ImprovementMemoryStore.insert_row(
            "improvement_sessions",
            {
                "id": session_id,
                "execution_id": execution_id,
                "session_data": json.dumps({"objective": objective[:200], "status": status}),
                "status": "running",
                "dry_run": 1 if dry_run else 0,
                "created_at": now,
                "completed_at": None,
            },
        )

        perf_snapshot = workflow.get("performance_snapshot")
        quality = workflow.get("final_quality_score")

        try:
            results = await ImprovementCoordinator.run_all_optimizers(
                execution_id,
                status,
                objective,
                workflow,
                emit_fn,
                dry_run=dry_run,
                perf_snapshot=perf_snapshot,
                quality_score=float(quality) if quality is not None else None,
            )
        except Exception as exc:
            logger.warning("Self-improvement cycle failed: %s", exc)
            results = {"error": str(exc)}

        safety = results.get("safety") or {}
        if safety.get("rejectedActions"):
            for action in safety["rejectedActions"]:
                await ImprovementSafety.audit(
                    session_id, "rejected", action, replayable=True
                )

        rollback_id = None
        regression = results.get("regression") or {}
        if not regression.get("healthy", True):
            rollback_id = await ImprovementSafety.record_rollback(
                session_id,
                "benchmark_regression",
                {"results": results},
            )
            await emit_fn(
                execution_id,
                "optimization_rollback_triggered",
                "self_improvement",
                "Rollback recorded due to benchmark regression",
                rollbackId=rollback_id,
                sessionId=session_id,
            )

        reasoning = cls._explain_cycle(results, dry_run)
        await ImprovementSafety.audit(
            session_id,
            "cycle_completed",
            {"reasoning": reasoning, "dryRun": dry_run, "resultsKeys": list(results.keys())},
        )

        completed = datetime.now(timezone.utc).isoformat()
        from app.database.database import get_db

        db = await get_db()
        await db.execute(
            """UPDATE improvement_sessions
               SET status = 'completed', session_data = ?, completed_at = ?
               WHERE id = ?""",
            (json.dumps({"results": results, "reasoning": reasoning}), completed, session_id),
        )
        await db.commit()

        ctx.set_workflow_memory(
            "self_improvement_cycle",
            {"sessionId": session_id, "dryRun": dry_run, "reasoning": reasoning, "results": results},
        )
        await ctx.persist()

        await emit_fn(
            execution_id,
            "improvement_cycle_completed",
            "self_improvement",
            reasoning.get("summary", "Cycle completed"),
            sessionId=session_id,
            dryRun=dry_run,
            rollbackId=rollback_id,
        )

        return {
            "sessionId": session_id,
            "dryRun": dry_run,
            "reasoning": reasoning,
            "results": results,
        }

    @classmethod
    def _explain_cycle(cls, results: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        parts: list[str] = []
        if results.get("heuristic"):
            parts.append("heuristic weights evolved from execution patterns")
        if results.get("benchmark"):
            parts.append("benchmark trends analyzed")
        if results.get("regression", {}).get("regressions"):
            parts.append("regression detected — rollback path prepared")
        mode = "dry-run (no profile writes)" if dry_run else "applied bounded profiles"
        return {
            "summary": f"Self-improvement cycle ({mode}): " + "; ".join(parts) or "no changes",
            "inspectable": True,
            "replayable": True,
            "modifiesSourceCode": False,
        }

    @classmethod
    async def get_dashboard_snapshot(cls, limit: int = 20) -> dict[str, Any]:
        await ImprovementMemoryStore.ensure_tables()
        return {
            "sessions": await ImprovementMemoryStore.query_recent("improvement_sessions", limit=limit),
            "heuristicGenerations": await ImprovementMemoryStore.query_recent(
                "heuristic_generations", limit=limit
            ),
            "mutations": await ImprovementMemoryStore.query_recent("strategy_mutations", limit=limit),
            "audits": await ImprovementMemoryStore.query_recent("improvement_audits", limit=limit),
            "regressions": await ImprovementMemoryStore.query_recent("regression_events", limit=limit),
            "promptVariants": await ImprovementMemoryStore.query_recent("prompt_variants", limit=limit),
            "policy": await EvolutionPolicy.get_active_policy(),
        }

    @classmethod
    async def replay_session(cls, session_id: str) -> dict[str, Any] | None:
        await ImprovementMemoryStore.ensure_tables()
        from app.database.database import get_db

        db = await get_db()
        cursor = await db.execute(
            "SELECT session_data, dry_run FROM improvement_sessions WHERE id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        audits = await db.execute(
            "SELECT audit_data, action_type FROM improvement_audits WHERE session_id = ?",
            (session_id,),
        )
        audit_rows = await audits.fetchall()
        return {
            "sessionId": session_id,
            "dryRun": bool(row["dry_run"]),
            "sessionData": json.loads(row["session_data"]),
            "audits": [
                {"action": a["action_type"], "data": json.loads(a["audit_data"])}
                for a in audit_rows
            ],
        }
