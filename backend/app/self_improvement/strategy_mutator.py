"""
Strategy mutation + ranking — bounded heuristic variants, no code changes.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.self_improvement.improvement_memory import ImprovementMemoryStore
from app.self_improvement.safety_limits import clamp_drift, limits_from_settings


class StrategyMutator:
    @classmethod
    async def mutate_and_rank(
        cls,
        parent_strategy: dict[str, Any],
        execution_signals: dict[str, Any],
    ) -> list[dict[str, Any]]:
        limits = limits_from_settings()
        base_weight = float(parent_strategy.get("weight", 0.5))
        variants: list[dict[str, Any]] = []

        mutations = [
            ("retry_reduction", -0.05, "Reduce retry aggressiveness after success"),
            ("depth_increase", 0.03, "Increase planning depth for complex tasks"),
            ("tool_grounding", 0.04, "Prefer tool-grounded paths"),
        ]

        if execution_signals.get("status") == "failed":
            mutations.append(("recovery_boost", 0.06, "Boost recovery strategy weight"))

        for mtype, delta, reason in mutations:
            new_weight = clamp_drift(base_weight, base_weight + delta, limits.max_parameter_drift)
            mut_id = f"mut-{uuid.uuid4().hex[:12]}"
            fitness = cls._score_fitness(mtype, execution_signals)
            row = {
                "id": mut_id,
                "parent_id": parent_strategy.get("id", ""),
                "mutation_type": mtype,
                "mutation_data": json.dumps(
                    {"weight": new_weight, "reason": reason, "parentWeight": base_weight}
                ),
                "fitness_score": fitness,
                "lineage": json.dumps(
                    [parent_strategy.get("id", "root"), mut_id]
                ),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await ImprovementMemoryStore.insert_row("strategy_mutations", row)
            variants.append({**row, "mutationData": json.loads(row["mutation_data"])})

        return sorted(variants, key=lambda v: v["fitness_score"], reverse=True)

    @classmethod
    def _score_fitness(cls, mutation_type: str, signals: dict[str, Any]) -> float:
        base = 0.5
        if signals.get("status") == "completed":
            base += 0.15
        if mutation_type == "retry_reduction" and int(signals.get("retryCount", 0)) > 2:
            base += 0.1
        if mutation_type == "tool_grounding" and signals.get("usedTools"):
            base += 0.12
        return min(1.0, base)
