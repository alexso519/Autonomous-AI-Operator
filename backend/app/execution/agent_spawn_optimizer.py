"""
Agent spawn optimization — reduce unnecessary agents via complexity estimation.

Persists spawn rationale for observability.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db
from app.services.task_analyzer import (
    Complexity,
    RequiredAgent,
    TaskAnalysis,
    TaskType,
)

logger = logging.getLogger(__name__)


@dataclass
class SpawnPlan:
    agents: list[RequiredAgent]
    mode: str
    agent_count: int
    rationale: list[str] = field(default_factory=list)
    complexity_score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "agentCount": self.agent_count,
            "rationale": self.rationale,
            "complexityScore": self.complexity_score,
            "agents": [a.to_dict() for a in self.agents],
        }


class AgentSpawnOptimizer:
    """Optimize agent count and roles based on task profile."""

    ROLE_REUSE_MAP: dict[str, str] = {
        "content writer": "Synthesizer",
        "insights generator": "Synthesizer",
        "content editor": "Quality Reviewer",
    }

    @classmethod
    def estimate_complexity_score(cls, analysis: TaskAnalysis) -> int:
        score = 0
        obj_len = len(analysis.objective)
        if obj_len > 200:
            score += 2
        elif obj_len > 80:
            score += 1

        if analysis.complexity == Complexity.COMPLEX:
            score += 3
        elif analysis.complexity == Complexity.MODERATE:
            score += 2
        else:
            score += 0

        if analysis.task_type in (TaskType.RESEARCH, TaskType.ANALYSIS):
            score += 2
        if len(analysis.keywords) > 5:
            score += 1

        return score

    @classmethod
    def optimize(cls, analysis: TaskAnalysis) -> SpawnPlan:
        """Return optimized agent list with spawn rationale."""
        agents = list(analysis.required_agents)
        rationale: list[str] = []
        score = cls.estimate_complexity_score(analysis)

        # Simple tasks: cap at 2 agents, lightweight mode
        if analysis.complexity == Complexity.SIMPLE and score <= 2:
            agents = cls._trim_to_pipeline(agents, max_agents=2)
            mode = "lightweight"
            rationale.append("simple_task_capped_at_2_agents")
        elif analysis.task_type == TaskType.RESEARCH and score <= 4:
            agents = cls._research_pipeline()
            mode = "research"
            rationale.append("research_pipeline_planner_researcher_synthesizer")
        elif analysis.task_type == TaskType.ANALYSIS and analysis.complexity == Complexity.COMPLEX:
            agents = cls._full_analysis_pipeline(agents)
            mode = "full_analysis"
            rationale.append("complex_analysis_full_pipeline")
        elif score <= 3:
            agents = cls._trim_to_pipeline(agents, max_agents=2)
            mode = "compact"
            rationale.append("low_complexity_compact_mode")
        else:
            agents = cls._dedupe_roles(agents)
            mode = "standard"
            rationale.append("standard_deduped_pipeline")

        agents = cls._normalize_sequence(agents)
        return SpawnPlan(
            agents=agents,
            mode=mode,
            agent_count=len(agents),
            rationale=rationale,
            complexity_score=score,
        )

    @classmethod
    def _research_pipeline(cls) -> list[RequiredAgent]:
        return [
            RequiredAgent(
                role="Research Planner",
                goal="Outline research scope and key questions",
                sequence_order=0,
                tools=["web_search"],
            ),
            RequiredAgent(
                role="Research Analyst",
                goal="Gather credible information using available tools",
                sequence_order=1,
                tools=["web_search", "summarization"],
            ),
            RequiredAgent(
                role="Synthesizer",
                goal="Produce a coherent report from findings",
                sequence_order=2,
                tools=["summarization"],
            ),
        ]

    @classmethod
    def _full_analysis_pipeline(cls, base: list[RequiredAgent]) -> list[RequiredAgent]:
        if len(base) >= 3:
            return cls._dedupe_roles(base)
        return [
            RequiredAgent(
                role="Data Analyst",
                goal="Analyze data and identify patterns",
                sequence_order=0,
            ),
            RequiredAgent(
                role="Insights Generator",
                goal="Synthesize actionable conclusions",
                sequence_order=1,
            ),
            RequiredAgent(
                role="Quality Reviewer",
                goal="Validate accuracy and completeness",
                sequence_order=2,
            ),
        ]

    @classmethod
    def _trim_to_pipeline(
        cls, agents: list[RequiredAgent], max_agents: int
    ) -> list[RequiredAgent]:
        if len(agents) <= max_agents:
            return agents
        # Keep first worker + last synthesizer/reviewer if present
        kept: list[RequiredAgent] = [agents[0]]
        tail = agents[-1]
        if tail.role != agents[0].role and len(kept) < max_agents:
            kept.append(tail)
        return kept[:max_agents]

    @classmethod
    def _dedupe_roles(cls, agents: list[RequiredAgent]) -> list[RequiredAgent]:
        seen: set[str] = set()
        result: list[RequiredAgent] = []
        for a in agents:
            role_key = a.role.lower().strip()
            mapped = cls.ROLE_REUSE_MAP.get(role_key, a.role)
            norm = mapped.lower()
            if norm in seen:
                continue
            seen.add(norm)
            merged = RequiredAgent(
                role=mapped,
                goal=a.goal,
                backstory=a.backstory,
                sequence_order=a.sequence_order,
                tools=a.tools,
                execution_constraints=a.execution_constraints,
                requires_approval=a.requires_approval,
            )
            result.append(merged)
        return result if result else agents

    @classmethod
    def _normalize_sequence(cls, agents: list[RequiredAgent]) -> list[RequiredAgent]:
        for i, a in enumerate(agents):
            a.sequence_order = i
        return agents

    @classmethod
    async def persist_spawn_plan(
        cls,
        execution_id: str,
        plan: SpawnPlan,
        task_type: str,
    ) -> None:
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO agent_spawn_plans
                   (id, execution_id, mode, agent_count, task_type, rationale, plan_data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex[:16],
                    execution_id,
                    plan.mode,
                    plan.agent_count,
                    task_type,
                    json.dumps(plan.rationale),
                    json.dumps(plan.to_dict()),
                    now,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Failed to persist spawn plan: %s", exc)

    @classmethod
    def apply_to_analysis(cls, analysis: TaskAnalysis) -> TaskAnalysis:
        """Return analysis with optimized required_agents."""
        plan = cls.optimize(analysis)
        analysis.required_agents = plan.agents
        analysis.estimated_steps = len(plan.agents)
        analysis.reasoning = (
            f"{analysis.reasoning} | Spawn mode: {plan.mode}, "
            f"agents: {plan.agent_count}, score: {plan.complexity_score}"
        )
        return analysis
