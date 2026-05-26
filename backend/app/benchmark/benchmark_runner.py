"""
Autonomous benchmark runner — executes benchmark tasks and persists metrics.

Runs against the real autonomous execution pipeline for regression detection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.benchmark.benchmark_tasks import BENCHMARK_TASKS, BenchmarkTask, get_task
from app.database.database import get_db
from app.execution.hallucination_guard import HallucinationGuard

logger = logging.getLogger(__name__)


class BenchmarkRunner:
    """Execute benchmark suite and record measurable runtime quality."""

    _running: bool = False

    @classmethod
    async def list_tasks(cls) -> list[dict[str, Any]]:
        return [
            {
                "id": t.id,
                "category": t.category,
                "name": t.name,
                "objective": t.objective,
                "description": t.description,
                "expectsTools": t.expects_tools,
                "maxDurationSeconds": t.max_duration_seconds,
            }
            for t in BENCHMARK_TASKS
        ]

    @classmethod
    async def run_suite(
        cls,
        task_ids: list[str] | None = None,
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        """Run full or partial benchmark suite."""
        if cls._running:
            return {"status": "busy", "message": "Benchmark suite already running"}

        tasks = cls._resolve_tasks(task_ids, categories)
        if not tasks:
            return {"status": "error", "message": "No matching benchmark tasks"}

        suite_id = uuid.uuid4().hex[:16]
        cls._running = True
        results: list[dict[str, Any]] = []

        try:
            for task in tasks:
                result = await cls.run_task(task, suite_id=suite_id)
                results.append(result)
        finally:
            cls._running = False

        summary = cls._summarize_suite(results)
        await cls._persist_suite_run(suite_id, results, summary)
        return {"suiteId": suite_id, "results": results, "summary": summary}

    @classmethod
    async def run_task(
        cls,
        task: BenchmarkTask | str,
        suite_id: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(task, str):
            resolved = get_task(task)
            if not resolved:
                return {"taskId": task, "success": False, "error": "unknown_task"}
            task = resolved

        run_id = uuid.uuid4().hex[:16]
        started = datetime.now(timezone.utc)
        execution_id: str | None = None
        success = False
        error: str | None = None

        try:
            from app.execution.runtime_hardening import ExecutionPriority, RuntimeHardening
            from app.execution.autonomous_service import AutonomousExecutionService

            RuntimeHardening.enqueue(
                run_id, ExecutionPriority.BENCHMARK, objective=task.objective
            )

            service = AutonomousExecutionService()
            start_result = await service.start(
                objective=task.objective,
                workflow_name=f"Benchmark: {task.name}",
            )
            execution_id = start_result["executionId"]

            final_status = await cls._wait_for_execution(
                execution_id, timeout=task.max_duration_seconds
            )
            success = final_status == "completed"
            if final_status == "timeout":
                error = "benchmark_timeout"
                success = task.success_criteria.get("allow_failure", False)
            elif final_status == "failed":
                error = "execution_failed"
                success = task.success_criteria.get("allow_failure", False)

        except Exception as exc:
            error = str(exc)[:500]
            logger.warning("Benchmark task %s failed: %s", task.id, exc)

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        metrics = await cls._collect_metrics(execution_id, task, duration)
        metrics["success"] = success and cls._evaluate_criteria(task, metrics)
        metrics["error"] = error

        await cls._persist_run(
            run_id=run_id,
            suite_id=suite_id,
            task=task,
            execution_id=execution_id,
            metrics=metrics,
            started_at=started.isoformat(),
        )
        return {"runId": run_id, "taskId": task.id, **metrics}

    @classmethod
    async def replay_run(cls, run_id: str) -> dict[str, Any]:
        db = await get_db()
        cursor = await db.execute(
            "SELECT task_id FROM benchmark_runs WHERE id = ?", (run_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return {"status": "error", "message": "Run not found"}
        task = get_task(row["task_id"])
        if not task:
            return {"status": "error", "message": "Task definition missing"}
        result = await cls.run_task(task)
        return {"status": "replayed", "originalRunId": run_id, "result": result}

    @classmethod
    async def compare_runs(cls, run_id_a: str, run_id_b: str) -> dict[str, Any]:
        db = await get_db()
        runs = {}
        for rid in (run_id_a, run_id_b):
            cursor = await db.execute(
                "SELECT * FROM benchmark_runs WHERE id = ?", (rid,)
            )
            row = await cursor.fetchone()
            if not row:
                return {"status": "error", "message": f"Run {rid} not found"}
            runs[rid] = dict(row)
            runs[rid]["metrics"] = json.loads(row["metrics_data"] or "{}")

        ma, mb = runs[run_id_a]["metrics"], runs[run_id_b]["metrics"]
        return {
            "runA": run_id_a,
            "runB": run_id_b,
            "delta": {
                "durationSeconds": round(mb.get("durationSeconds", 0) - ma.get("durationSeconds", 0), 2),
                "retryCount": mb.get("retryCount", 0) - ma.get("retryCount", 0),
                "qualityScore": round(
                    (mb.get("qualityScore") or 0) - (ma.get("qualityScore") or 0), 3
                ),
                "hallucinationRisk": round(
                    (mb.get("hallucinationRisk") or 0) - (ma.get("hallucinationRisk") or 0), 3
                ),
                "toolCalls": mb.get("toolCalls", 0) - ma.get("toolCalls", 0),
                "cacheHitRate": round(
                    (mb.get("cacheHitRate") or 0) - (ma.get("cacheHitRate") or 0), 3
                ),
            },
            "metricsA": ma,
            "metricsB": mb,
        }

    @classmethod
    async def get_history(cls, limit: int = 50) -> list[dict[str, Any]]:
        db = await get_db()
        cursor = await db.execute(
            """SELECT id, suite_id, task_id, category, execution_id, success,
                      duration_seconds, metrics_data, created_at
               FROM benchmark_runs ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "runId": r["id"],
                "suiteId": r["suite_id"],
                "taskId": r["task_id"],
                "category": r["category"],
                "executionId": r["execution_id"],
                "success": bool(r["success"]),
                "durationSeconds": r["duration_seconds"],
                "metrics": json.loads(r["metrics_data"] or "{}"),
                "createdAt": r["created_at"],
            }
            for r in rows
        ]

    @classmethod
    async def get_trends(cls, days: int = 30) -> dict[str, Any]:
        db = await get_db()
        cursor = await db.execute(
            f"""SELECT DATE(created_at) as day,
                       COUNT(*) as runs,
                       AVG(duration_seconds) as avg_duration,
                       AVG(CAST(json_extract(metrics_data, '$.retryCount') AS REAL)) as avg_retries,
                       AVG(CAST(json_extract(metrics_data, '$.qualityScore') AS REAL)) as avg_quality,
                       AVG(CAST(json_extract(metrics_data, '$.hallucinationRisk') AS REAL)) as avg_hallucination,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes
                FROM benchmark_runs
                WHERE created_at > datetime('now', '-{int(days)} days')
                GROUP BY DATE(created_at) ORDER BY day ASC"""
        )
        rows = await cursor.fetchall()
        daily = [
            {
                "day": r["day"],
                "runs": r["runs"],
                "avgDuration": round(r["avg_duration"] or 0, 1),
                "avgRetries": round(r["avg_retries"] or 0, 2),
                "avgQuality": round(r["avg_quality"] or 0, 2),
                "avgHallucination": round(r["avg_hallucination"] or 0, 2),
                "successRate": round((r["successes"] or 0) / max(r["runs"] or 1, 1) * 100, 1),
            }
            for r in rows
        ]
        return {"daily": daily, "days": days}

    @classmethod
    async def export_report(cls, suite_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        history = await cls.get_history(limit=limit)
        if suite_id:
            history = [h for h in history if h.get("suiteId") == suite_id]
        trends = await cls.get_trends()
        return {
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "suiteId": suite_id,
            "runs": history,
            "trends": trends,
            "tasks": await cls.list_tasks(),
        }

    @classmethod
    def _resolve_tasks(
        cls,
        task_ids: list[str] | None,
        categories: list[str] | None,
    ) -> list[BenchmarkTask]:
        tasks = list(BENCHMARK_TASKS)
        if task_ids:
            id_set = set(task_ids)
            tasks = [t for t in tasks if t.id in id_set]
        if categories:
            cat_set = set(categories)
            tasks = [t for t in tasks if t.category in cat_set]
        return tasks

    @classmethod
    async def _wait_for_execution(cls, execution_id: str, timeout: int) -> str:
        db = await get_db()
        deadline = __import__("time").monotonic() + timeout
        while __import__("time").monotonic() < deadline:
            cursor = await db.execute(
                "SELECT status FROM executions WHERE id = ?", (execution_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return "failed"
            status = row["status"]
            if status in ("completed", "failed", "cancelled", "rejected", "orphaned"):
                return status
            await asyncio.sleep(5)
        return "timeout"

    @classmethod
    async def _collect_metrics(
        cls,
        execution_id: str | None,
        task: BenchmarkTask,
        duration: float,
    ) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "taskId": task.id,
            "category": task.category,
            "durationSeconds": round(duration, 2),
            "retryCount": 0,
            "toolCalls": 0,
            "cacheHits": 0,
            "cacheHitRate": 0.0,
            "qualityScore": None,
            "hallucinationRisk": 0.0,
            "hallucinationIndicators": [],
            "modelRouting": [],
            "contextTokenEstimate": 0,
            "executionId": execution_id,
        }
        if not execution_id:
            return metrics

        db = await get_db()

        m_cursor = await db.execute(
            "SELECT * FROM execution_metrics WHERE execution_id = ? ORDER BY created_at DESC LIMIT 1",
            (execution_id,),
        )
        m = await m_cursor.fetchone()
        if m:
            metrics["retryCount"] = m["retry_count"] or 0
            metrics["toolCalls"] = m["tool_count"] or 0
            metrics["cacheHits"] = m["cache_hits"] or 0
            metrics["qualityScore"] = m["quality_avg"]
            tool_total = max(metrics["toolCalls"], 1)
            metrics["cacheHitRate"] = round(metrics["cacheHits"] / tool_total, 3)

        q_cursor = await db.execute(
            """SELECT AVG(hallucination_risk) as hr, AVG(overall_score) as oq
               FROM execution_quality_scores WHERE execution_id = ?""",
            (execution_id,),
        )
        q = await q_cursor.fetchone()
        if q and q["hr"] is not None:
            metrics["hallucinationRisk"] = round(q["hr"], 3)
        if q and q["oq"] is not None and metrics["qualityScore"] is None:
            metrics["qualityScore"] = round(q["oq"], 3)

        route_cursor = await db.execute(
            "SELECT model, tier FROM model_routing_history WHERE execution_id = ?",
            (execution_id,),
        )
        metrics["modelRouting"] = [
            {"model": r["model"], "tier": r["tier"]} for r in await route_cursor.fetchall()
        ]

        exec_cursor = await db.execute(
            "SELECT shared_memory, output FROM executions WHERE id = ?",
            (execution_id,),
        )
        exec_row = await exec_cursor.fetchone()
        if exec_row:
            shared = json.loads(exec_row["shared_memory"] or "{}")
            outputs = shared.get("outputs", {})
            tool_calls = shared.get("workflow_memory", {}).get("tool_calls", [])
            from app.execution.token_budget_optimizer import estimate_context_tokens

            ctx_text = " ".join(
                str(v.get("output", "")) for v in outputs.values() if isinstance(v, dict)
            )
            metrics["contextTokenEstimate"] = estimate_context_tokens(
                shared.get("initial_context", ""), outputs, tool_calls
            )

            # Hallucination assessment on final output
            output_payload = json.loads(exec_row["output"] or "{}")
            final_text = output_payload.get("synthesized") or output_payload.get("executive_summary") or ctx_text[:2000]
            assessment = HallucinationGuard.assess(
                str(final_text), goal=task.objective, tool_calls=tool_calls, is_research=task.expects_tools
            )
            metrics["hallucinationRisk"] = max(metrics["hallucinationRisk"], assessment.hallucination_risk)
            metrics["hallucinationIndicators"] = assessment.indicators

        perf_cursor = await db.execute(
            "SELECT snapshot_data FROM runtime_performance_snapshots WHERE execution_id = ? LIMIT 1",
            (execution_id,),
        )
        perf = await perf_cursor.fetchone()
        if perf:
            pdata = json.loads(perf["snapshot_data"])
            metrics["performance"] = {
                "bottlenecks": pdata.get("bottlenecks", []),
                "recommendations": pdata.get("recommendations", []),
            }

        return metrics

    @classmethod
    def _evaluate_criteria(cls, task: BenchmarkTask, metrics: dict[str, Any]) -> bool:
        c = task.success_criteria
        if c.get("min_tool_calls") and metrics.get("toolCalls", 0) < c["min_tool_calls"]:
            return False
        if c.get("max_hallucination_risk") and metrics.get("hallucinationRisk", 1) > c["max_hallucination_risk"]:
            return False
        if c.get("min_quality") and (metrics.get("qualityScore") or 0) < c["min_quality"]:
            return False
        if c.get("max_retries") and metrics.get("retryCount", 0) > c["max_retries"]:
            return False
        if c.get("max_duration") and metrics.get("durationSeconds", 0) > c["max_duration"]:
            return False
        return True

    @classmethod
    def _summarize_suite(cls, results: list[dict[str, Any]]) -> dict[str, Any]:
        if not results:
            return {}
        successes = sum(1 for r in results if r.get("success"))
        return {
            "total": len(results),
            "successes": successes,
            "failures": len(results) - successes,
            "successRate": round(successes / len(results) * 100, 1),
            "avgDuration": round(
                sum(r.get("durationSeconds", 0) for r in results) / len(results), 2
            ),
            "avgRetries": round(
                sum(r.get("retryCount", 0) for r in results) / len(results), 2
            ),
            "avgHallucination": round(
                sum(r.get("hallucinationRisk", 0) for r in results) / len(results), 3
            ),
        }

    @classmethod
    async def _persist_run(
        cls,
        run_id: str,
        suite_id: str | None,
        task: BenchmarkTask,
        execution_id: str | None,
        metrics: dict[str, Any],
        started_at: str,
    ) -> None:
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO benchmark_runs
               (id, suite_id, task_id, category, execution_id, success,
                duration_seconds, metrics_data, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                suite_id or "",
                task.id,
                task.category,
                execution_id or "",
                1 if metrics.get("success") else 0,
                metrics.get("durationSeconds", 0),
                json.dumps(metrics),
                now,
            ),
        )
        await db.commit()

    @classmethod
    async def _persist_suite_run(
        cls,
        suite_id: str,
        results: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> None:
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO benchmark_suites (id, summary_data, results_count, created_at)
               VALUES (?, ?, ?, ?)""",
            (suite_id, json.dumps(summary), len(results), now),
        )
        await db.commit()


async def run_benchmarks_on_startup() -> None:
    """Optional startup benchmark if BENCHMARK_ON_STARTUP=1."""
    import os

    if os.environ.get("BENCHMARK_ON_STARTUP", "").lower() not in ("1", "true", "yes"):
        return
    logger.info("Running startup benchmark suite (subset)...")
    from app.benchmark.benchmark_tasks import tasks_by_category

    # Quick subset: one task per category to avoid long startup
    subset = []
    seen: set[str] = set()
    for t in BENCHMARK_TASKS:
        if t.category not in seen:
            subset.append(t.id)
            seen.add(t.category)
    await BenchmarkRunner.run_suite(task_ids=subset[:4])
