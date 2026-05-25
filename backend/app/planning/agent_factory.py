from typing import Any, List

from app.services.task_analyzer import (
    TaskAnalysis,
    RequiredAgent,
    TaskType,
    Complexity,
    RiskLevel,
)


class DynamicAgentFactory:
    """Rule-based deterministic agent generator for MVP.

    Generates agent definitions (as RequiredAgent instances) from a
    TaskAnalysis. Deterministic mapping of task types -> tools + constraints.
    """

    TOOL_MAP: dict[TaskType, list[str]] = {
        TaskType.RESEARCH: ["web_search", "summarization"],
        TaskType.ANALYSIS: ["data_processing", "summarization"],
        TaskType.CONTENT_CREATION: ["writing", "formatting"],
        TaskType.CODING: ["filesystem", "terminal", "code_analysis"],
        TaskType.PLANNING: ["timeline", "estimation"],
        TaskType.REVIEW: ["lint", "qa"],
        TaskType.SYNTHESIS: ["summarization", "integration"],
        TaskType.CUSTOM: ["writing"],
    }

    def generate_agents(self, analysis: TaskAnalysis) -> List[RequiredAgent]:
        """Return a list of RequiredAgent objects enriched with tools,
        execution constraints, and approval flags.
        """
        agents: list[RequiredAgent] = []

        base_tools = self.TOOL_MAP.get(analysis.task_type, ["writing"])

        for a in analysis.required_agents:
            # Deterministic role normalization
            role = a.role.strip()

            # Assign tools: start from base tools, append role-specific hints
            tools = list(base_tools)
            if "review" in role.lower() or "qa" in role.lower():
                tools = [t for t in tools if t != "terminal"] + ["lint", "qa"]

            # Execution constraints: timeout scales with complexity
            timeout = 180 if analysis.complexity == Complexity.SIMPLE else 300
            if analysis.complexity == Complexity.COMPLEX:
                timeout = 600

            execution_constraints = {"timeout": timeout}

            # Approval: if the overall task is medium/high risk, mark agents
            requires_approval = analysis.risk_level != RiskLevel.LOW

            enriched = RequiredAgent(
                role=role,
                goal=a.goal,
                backstory=a.backstory or f"Experienced {role} focused on {analysis.task_type.value}",
                sequence_order=a.sequence_order,
                tools=tools,
                execution_constraints=execution_constraints,
                requires_approval=requires_approval,
            )

            agents.append(enriched)

        return agents


_factory_instance: DynamicAgentFactory | None = None


def get_agent_factory() -> DynamicAgentFactory:
    global _factory_instance
    if _factory_instance is None:
        _factory_instance = DynamicAgentFactory()
    return _factory_instance
