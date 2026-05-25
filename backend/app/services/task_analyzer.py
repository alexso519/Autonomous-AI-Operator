"""
Task Analyzer — Autonomous task decomposition service.

Analyzes a high-level user objective and generates structured planning metadata:
  - Task type classification (research, analysis, creation, coding, planning, etc.)
  - Required agent roles
  - Task complexity estimation
  - Risk assessment
  - Required approvals

This service sits ABOVE the execution engine.
It doesn't execute workflows—it analyzes tasks and generates execution plans
that feed into the existing engine via WorkflowPlanner.

Usage:
    analyzer = TaskAnalyzer()
    analysis = await analyzer.analyze("Research AI chip market and write report")
    # Returns: TaskAnalysis with type, agents, complexity, etc.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from crewai import Agent as CrewAgent, Task as CrewTask
from langchain_community.chat_models import ChatOllama

from app.config.settings import settings

logger = logging.getLogger(__name__)


# ── Enums ─────────────────────────────────────────────────────


class TaskType(str, Enum):
    """High-level task categories."""

    RESEARCH = "research"
    ANALYSIS = "analysis"
    CONTENT_CREATION = "content_creation"
    CODING = "coding"
    PLANNING = "planning"
    REVIEW = "review"
    SYNTHESIS = "synthesis"
    CUSTOM = "custom"


class Complexity(str, Enum):
    """Task complexity estimation."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class RiskLevel(str, Enum):
    """Security/safety risk assessment."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Data Models ───────────────────────────────────────────────


@dataclass
class RequiredAgent:
    """Specification for a dynamically generated agent."""

    role: str
    """Agent's professional role (e.g., 'Research Analyst', 'Content Writer')"""

    goal: str
    """Specific objective for this agent in the task"""

    backstory: str = ""
    """Agent's background (auto-generated if empty)"""

    sequence_order: int = 0
    """Execution order (0-indexed) for sequential workflow"""

    tools: list[str] = field(default_factory=list)
    """Optional tool names (e.g., 'web_search', 'file_read')"""

    execution_constraints: dict[str, Any] = field(default_factory=dict)
    """Execution constraints (timeouts, resource limits, etc.)"""

    requires_approval: bool = False
    """Whether this agent's actions require explicit human approval."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "role": self.role,
            "goal": self.goal,
            "backstory": self.backstory,
            "sequenceOrder": self.sequence_order,
            "tools": self.tools,
            "executionConstraints": self.execution_constraints,
            "requiresApproval": self.requires_approval,
        }


@dataclass
class TaskAnalysis:
    """Complete task analysis result."""

    objective: str
    """Original user objective"""

    task_type: TaskType
    """Classified task category"""

    complexity: Complexity
    """Estimated complexity level"""

    risk_level: RiskLevel
    """Estimated security/safety risk"""

    required_agents: list[RequiredAgent] = field(default_factory=list)
    """Agents needed to complete the task, in execution order"""

    keywords: list[str] = field(default_factory=list)
    """Extracted task keywords for context"""

    estimated_steps: int = 0
    """Estimated number of workflow steps"""

    requires_human_approval: bool = False
    """Whether the task requires human approval gates"""

    reasoning: str = ""
    """Explanation of the analysis (for debugging)"""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "objective": self.objective,
            "taskType": self.task_type.value,
            "complexity": self.complexity.value,
            "riskLevel": self.risk_level.value,
            "requiredAgents": [a.to_dict() for a in self.required_agents],
            "keywords": self.keywords,
            "estimatedSteps": self.estimated_steps,
            "requiresHumanApproval": self.requires_human_approval,
            "reasoning": self.reasoning,
        }


# ── Task Type Detection Rules ──────────────────────────────────


_TASK_TYPE_KEYWORDS: dict[TaskType, list[str]] = {
    TaskType.RESEARCH: [
        "research",
        "investigate",
        "explore",
        "find",
        "discover",
        "learn",
        "gather",
        "study",
    ],
    TaskType.ANALYSIS: [
        "analyze",
        "evaluate",
        "assess",
        "examine",
        "review",
        "critique",
        "compare",
        "contrast",
    ],
    TaskType.CONTENT_CREATION: [
        "write",
        "create",
        "generate",
        "compose",
        "draft",
        "author",
        "produce",
    ],
    TaskType.CODING: [
        "code",
        "program",
        "develop",
        "build",
        "implement",
        "debug",
        "refactor",
        "script",
    ],
    TaskType.PLANNING: [
        "plan",
        "organize",
        "structure",
        "schedule",
        "coordinate",
        "outline",
        "strategize",
    ],
    TaskType.REVIEW: [
        "review",
        "check",
        "verify",
        "validate",
        "proofread",
        "quality",
        "test",
    ],
    TaskType.SYNTHESIS: [
        "summarize",
        "synthesize",
        "combine",
        "integrate",
        "merge",
        "consolidate",
    ],
}


_AGENT_TEMPLATES: dict[TaskType, list[RequiredAgent]] = {
    TaskType.RESEARCH: [
        RequiredAgent(
            role="Research Analyst",
            goal="Investigate the topic thoroughly and gather credible information",
            sequence_order=0,
        ),
        RequiredAgent(
            role="Content Writer",
            goal="Transform research findings into a well-structured report",
            sequence_order=1,
        ),
    ],
    TaskType.ANALYSIS: [
        RequiredAgent(
            role="Data Analyst",
            goal="Examine and interpret the data to identify patterns and insights",
            sequence_order=0,
        ),
        RequiredAgent(
            role="Insights Generator",
            goal="Synthesize findings into actionable conclusions",
            sequence_order=1,
        ),
        RequiredAgent(
            role="Quality Reviewer",
            goal="Validate analysis accuracy and completeness",
            sequence_order=2,
        ),
    ],
    TaskType.CONTENT_CREATION: [
        RequiredAgent(
            role="Content Strategist",
            goal="Plan content structure and key messages",
            sequence_order=0,
        ),
        RequiredAgent(
            role="Content Writer",
            goal="Draft engaging, coherent content",
            sequence_order=1,
        ),
        RequiredAgent(
            role="Content Editor",
            goal="Polish content for clarity and consistency",
            sequence_order=2,
        ),
    ],
    TaskType.CODING: [
        RequiredAgent(
            role="Software Engineer",
            goal="Implement clean, well-documented, tested code",
            sequence_order=0,
        ),
        RequiredAgent(
            role="Code Reviewer",
            goal="Review code for quality, security, and best practices",
            sequence_order=1,
        ),
    ],
    TaskType.PLANNING: [
        RequiredAgent(
            role="Project Planner",
            goal="Break down the goal into structured, actionable steps",
            sequence_order=0,
        ),
        RequiredAgent(
            role="Risk Assessor",
            goal="Identify potential risks and mitigation strategies",
            sequence_order=1,
        ),
    ],
    TaskType.REVIEW: [
        RequiredAgent(
            role="Quality Assurance Lead",
            goal="Thoroughly review for consistency, accuracy, and completeness",
            sequence_order=0,
        ),
    ],
    TaskType.SYNTHESIS: [
        RequiredAgent(
            role="Research Analyst",
            goal="Gather and organize source materials",
            sequence_order=0,
        ),
        RequiredAgent(
            role="Synthesis Expert",
            goal="Integrate sources into a coherent synthesis",
            sequence_order=1,
        ),
    ],
}


# ── Task Analyzer ──────────────────────────────────────────────


class TaskAnalyzer:
    """
    Analyzes high-level user objectives and generates execution plans.

    This service:
    1. Classifies task types by keyword matching
    2. Estimates complexity from objective length/keywords
    3. Selects appropriate agent roles from templates
    4. Assesses risk (external calls, file operations, etc.)
    5. Generates structured TaskAnalysis
    """

    def __init__(self) -> None:
        """Initialize the analyzer."""
        self.llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_url,
            temperature=0.5,  # Lower temp for analysis consistency
        )
        logger.info("TaskAnalyzer initialized")

    async def analyze(self, objective: str) -> TaskAnalysis:
        """
        Analyze a user objective and generate a task analysis.

        Args:
            objective: The user's high-level task description

        Returns:
            TaskAnalysis with classification, agents, complexity, etc.
        """
        if not objective or not objective.strip():
            raise ValueError("Objective cannot be empty")

        objective = objective.strip()
        logger.info("Analyzing objective: %s", objective[:100])

        # 1. Detect task type
        task_type = self._detect_task_type(objective)

        # 2. Extract keywords
        keywords = self._extract_keywords(objective)

        # 3. Estimate complexity
        complexity = self._estimate_complexity(objective, task_type)

        # 4. Assess risk
        risk_level = self._assess_risk(objective)

        # 5. Select agent roles
        required_agents = self._select_agents(task_type, objective, complexity)

        # 6. Estimate steps
        estimated_steps = len(required_agents) + (1 if risk_level != RiskLevel.LOW else 0)

        # 7. Determine if approval is needed
        requires_approval = risk_level != RiskLevel.LOW

        reasoning = f"Detected {task_type.value} task. Requires {len(required_agents)} agents. Complexity: {complexity.value}. Risk: {risk_level.value}."

        return TaskAnalysis(
            objective=objective,
            task_type=task_type,
            complexity=complexity,
            risk_level=risk_level,
            required_agents=required_agents,
            keywords=keywords,
            estimated_steps=estimated_steps,
            requires_human_approval=requires_approval,
            reasoning=reasoning,
        )

    def _detect_task_type(self, objective: str) -> TaskType:
        """Detect task type by keyword matching."""
        objective_lower = objective.lower()
        match_scores: dict[TaskType, int] = {t: 0 for t in TaskType}

        for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in objective_lower:
                    match_scores[task_type] += 1

        # Find best match
        best_type = max(match_scores, key=match_scores.get)
        if match_scores[best_type] > 0:
            return best_type

        # Default to synthesis/custom if no keywords match
        return TaskType.SYNTHESIS

    def _extract_keywords(self, objective: str) -> list[str]:
        """Extract important keywords from the objective."""
        # Simple approach: take capitalized words and common keywords
        words = objective.split()
        keywords = []

        for word in words:
            cleaned = word.strip(".,!?;:")
            # Include capitalized words (likely proper nouns) and some keywords
            if len(cleaned) > 3 and (cleaned[0].isupper() or cleaned.lower() in {
                "research",
                "analysis",
                "report",
                "code",
                "plan",
                "create",
                "review",
                "market",
                "data",
            }):
                if cleaned not in keywords:
                    keywords.append(cleaned)

        return keywords[:10]  # Top 10 keywords

    def _estimate_complexity(
        self, objective: str, task_type: TaskType
    ) -> Complexity:
        """Estimate task complexity from objective length and type."""
        # Heuristics:
        # - Long objectives (>100 chars) → likely complex
        # - Multiple subtasks (keywords like "and", "also") → complex
        # - Analysis/Research typically moderate to complex
        # - Simple creation tasks are simple

        length_score = len(objective) // 20  # 1 point per 20 chars
        keyword_count = objective.lower().count(" and ") + objective.lower().count(" also ")

        complexity_score = length_score + keyword_count

        if task_type in {TaskType.RESEARCH, TaskType.ANALYSIS, TaskType.PLANNING}:
            complexity_score += 2

        if complexity_score >= 8:
            return Complexity.COMPLEX
        elif complexity_score >= 4:
            return Complexity.MODERATE
        else:
            return Complexity.SIMPLE

    def _assess_risk(self, objective: str) -> RiskLevel:
        """Assess security/safety risk level."""
        objective_lower = objective.lower()

        # High-risk indicators
        high_risk_keywords = {
            "delete",
            "remove",
            "drop",
            "exec",
            "system",
            "shell",
            "bash",
            "import",
            "curl",
            "http",
            "email",
            "send",
            "payment",
            "api",
            "key",
            "secret",
        }

        # Medium-risk indicators
        medium_risk_keywords = {
            "write",
            "create file",
            "save",
            "modify",
            "update",
            "fetch",
            "request",
        }

        high_risk_count = sum(1 for k in high_risk_keywords if k in objective_lower)
        medium_risk_count = sum(1 for k in medium_risk_keywords if k in objective_lower)

        if high_risk_count >= 2:
            return RiskLevel.HIGH
        elif high_risk_count == 1 or medium_risk_count >= 2:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _select_agents(
        self, task_type: TaskType, objective: str, complexity: Complexity
    ) -> list[RequiredAgent]:
        """Select and customize agent roles for the task."""
        # Get base agents from template
        base_agents = _AGENT_TEMPLATES.get(task_type, [])
        selected = list(base_agents)

        # For complex tasks, add a planner at the start
        if complexity == Complexity.COMPLEX and not any(
            a.role.lower().find("plan") >= 0 for a in selected
        ):
            planner = RequiredAgent(
                role="Task Planner",
                goal="Break down the objective into clear, sequential steps",
                sequence_order=0,
            )
            # Shift other agents down
            for agent in selected:
                agent.sequence_order += 1
            selected.insert(0, planner)

        # For all tasks, ensure we have a reviewer if complexity >= moderate
        if complexity != Complexity.SIMPLE and not any(
            "review" in a.role.lower() for a in selected
        ):
            reviewer = RequiredAgent(
                role="Quality Reviewer",
                goal="Review the work for accuracy, completeness, and quality",
                sequence_order=len(selected),
            )
            selected.append(reviewer)

        # Set final sequence order
        for i, agent in enumerate(selected):
            agent.sequence_order = i

        return selected


# ── Singleton instance ─────────────────────────────────────────


_analyzer_instance: TaskAnalyzer | None = None


def get_analyzer() -> TaskAnalyzer:
    """Get or create the singleton TaskAnalyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = TaskAnalyzer()
    return _analyzer_instance
