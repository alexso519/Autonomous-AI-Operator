"""
Adversarial benchmarking — self-generated stress tests integrated with BenchmarkRunner.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.self_improvement.evolution_governor import EvolutionGovernor
from app.self_improvement.meta_reasoning_memory import MetaReasoningMemory
from app.self_improvement.scenario_generator import ScenarioGenerator
from app.self_improvement.stress_evolution import StressEvolution

logger = logging.getLogger(__name__)
EmitFn = Callable[..., Awaitable[None]]


class AdversarialBenchmarking:
    @classmethod
    async def run_adversarial_pass(
        cls,
        execution_id: str,
        meta_reflection: dict[str, Any],
        emit_fn: EmitFn,
    ) -> dict[str, Any]:
        if not EvolutionGovernor.is_adversarial_enabled():
            return {"skipped": True, "reason": "adversarial_benchmarking_disabled"}

        scenarios = await ScenarioGenerator.generate_from_reflection(meta_reflection)
        runs: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()

        for scenario in scenarios:
            await emit_fn(
                execution_id,
                "scenario_generated",
                "self_improvement",
                f"Adversarial scenario: {scenario.get('scenarioType')}",
                scenarioId=scenario.get("id"),
                scenarioType=scenario.get("scenarioType"),
            )

            await emit_fn(
                execution_id,
                "adversarial_run_started",
                "self_improvement",
                f"Adversarial run started for {scenario.get('scenarioType')}",
                scenarioId=scenario.get("id"),
            )

            stress_result = await StressEvolution.run_stress_profile(scenario)
            benchmark_hint = await cls._integrate_benchmark_runner(scenario)

            run_id = f"ar-{uuid.uuid4().hex[:12]}"
            run_data = {
                "scenario": scenario,
                "stressResult": stress_result,
                "benchmarkHint": benchmark_hint,
                "recommendationOnly": True,
            }
            await MetaReasoningMemory.insert_row(
                "adversarial_runs",
                {
                    "id": run_id,
                    "scenario_id": scenario.get("id", ""),
                    "run_data": json.dumps(run_data),
                    "resilience_score": stress_result.get("resilienceScore", 0.5),
                    "status": "completed",
                    "created_at": now,
                },
            )
            runs.append({**run_data, "runId": run_id})

            await emit_fn(
                execution_id,
                "stress_test_completed",
                "self_improvement",
                f"Stress test completed — resilience {stress_result.get('resilienceScore', 0):.2f}",
                runId=run_id,
                resilienceScore=stress_result.get("resilienceScore"),
            )

            await emit_fn(
                execution_id,
                "resilience_profile_updated",
                "self_improvement",
                f"Resilience profile updated for {scenario.get('scenarioType')}",
                scenarioType=scenario.get("scenarioType"),
                score=stress_result.get("resilienceScore"),
            )

        await cls._integrate_infrastructure(execution_id, runs)

        return {"scenarios": scenarios, "runs": runs}

    @classmethod
    async def _integrate_benchmark_runner(cls, scenario: dict[str, Any]) -> dict[str, Any]:
        try:
            from app.benchmark.benchmark_runner import BenchmarkRunner

            tasks = await BenchmarkRunner.list_tasks()
            matching = [t for t in tasks if scenario.get("scenarioType", "") in t.get("category", "")]
            return {
                "benchmarkTasksAvailable": len(tasks),
                "matchingTasks": len(matching),
                "recommendRun": len(matching) > 0,
            }
        except Exception as exc:
            logger.debug("BenchmarkRunner integration skipped: %s", exc)
            return {"skipped": True}

    @classmethod
    async def _integrate_infrastructure(cls, execution_id: str, runs: list[dict[str, Any]]) -> None:
        try:
            from app.execution.runtime_hardening import RuntimeHardening

            avg_resilience = 0.0
            if runs:
                scores = [r.get("stressResult", {}).get("resilienceScore", 0.5) for r in runs]
                avg_resilience = sum(scores) / len(scores)
            if avg_resilience < 0.5:
                logger.info("Low resilience %s for %s — hardening review recommended", avg_resilience, execution_id)
        except Exception:
            pass

        try:
            from app.infrastructure.execution_recovery import ExecutionRecovery

            _ = ExecutionRecovery  # lazy availability check
        except Exception:
            pass

    @classmethod
    async def get_replay_data(cls, limit: int = 20) -> dict[str, Any]:
        return {
            "scenarios": await MetaReasoningMemory.query_recent("generated_scenarios", limit=limit),
            "runs": await MetaReasoningMemory.query_recent("adversarial_runs", limit=limit),
            "resilience": await StressEvolution.get_resilience_trends(limit=limit),
        }
