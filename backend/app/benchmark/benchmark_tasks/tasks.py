"""
Benchmark task definitions for autonomous runtime evaluation.

Each task targets a specific capability category with measurable expectations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BenchmarkTask:
    id: str
    category: str
    name: str
    objective: str
    description: str = ""
    expects_tools: bool = False
    expects_synthesis: bool = True
    max_duration_seconds: int = 600
    success_criteria: dict[str, Any] = field(default_factory=dict)


BENCHMARK_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        id="research_web_topic",
        category="research",
        name="Web Research",
        objective="Research the current state of local LLM inference tools and summarize key options.",
        description="Should invoke web_search and cite sources.",
        expects_tools=True,
        success_criteria={"min_tool_calls": 1, "max_hallucination_risk": 0.5},
    ),
    BenchmarkTask(
        id="summarize_bullets",
        category="summarization",
        name="Structured Summary",
        objective="Summarize the benefits and risks of autonomous AI agents in 5 bullet points with headings.",
        description="Tests concise structured output without external research.",
        expects_tools=False,
        success_criteria={"min_quality": 0.5, "max_duration": 300},
    ),
    BenchmarkTask(
        id="plan_project",
        category="planning",
        name="Project Planning",
        objective="Create a 3-phase plan to deploy a local AI operator with milestones and deliverables.",
        description="Tests planning structure and completeness.",
        expects_tools=False,
        success_criteria={"min_quality": 0.55},
    ),
    BenchmarkTask(
        id="tool_calculator",
        category="tool_usage",
        name="Calculator Tool",
        objective="Use the calculator tool to compute (847 * 23) + 156 and explain the result.",
        description="Must invoke calculator tool for numeric answer.",
        expects_tools=True,
        success_criteria={"required_tools": ["calculator"], "min_tool_calls": 1},
    ),
    BenchmarkTask(
        id="synthesis_report",
        category="synthesis",
        name="Multi-Agent Synthesis",
        objective="Analyze pros and cons of SQLite vs PostgreSQL for a single-user AI app, then recommend one.",
        description="Tests final synthesis quality across agents.",
        expects_tools=False,
        expects_synthesis=True,
        success_criteria={"min_quality": 0.5},
    ),
    BenchmarkTask(
        id="long_workflow",
        category="long_running",
        name="Long Workflow Stability",
        objective="Provide a comprehensive guide to setting up Ollama with Docker, including troubleshooting steps.",
        description="Tests stability over multi-agent long execution.",
        max_duration_seconds=900,
        success_criteria={"max_retries": 4, "min_quality": 0.45},
    ),
    BenchmarkTask(
        id="retry_recovery",
        category="retry_recovery",
        name="Retry Recovery",
        objective="Research recent AI safety guidelines and produce a verified summary with sources.",
        description="May trigger quality retries; measures recovery behavior.",
        expects_tools=True,
        success_criteria={"max_hallucination_risk": 0.55},
    ),
    BenchmarkTask(
        id="failure_handling",
        category="failure_handling",
        name="Graceful Failure",
        objective="Attempt to fetch data from https://invalid-domain-xyz-12345.example and report what happened.",
        description="Tests failure isolation and honest reporting.",
        expects_tools=True,
        success_criteria={"allow_failure": True, "max_hallucination_risk": 0.6},
    ),
    BenchmarkTask(
        id="coding_fix_tests",
        category="coding",
        name="Autonomous Test Fix",
        objective="Fix the failing tests in this repository",
        description="Tests repo analysis, patch generation, test reruns, and debug loop.",
        expects_tools=False,
        max_duration_seconds=300,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="browser_research",
        category="browser",
        name="Browser Research",
        objective="Open GitHub, research latest NVIDIA Blackwell news, and summarize findings",
        description="Tests browser navigation, screenshots, and evidence synthesis.",
        expects_tools=True,
        max_duration_seconds=300,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="long_term_recall",
        category="intelligence",
        name="Long-Term Recall",
        objective="Recall prior research on local LLM inference tools from semantic memory and summarize.",
        description="Tests cross-session semantic memory retrieval.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="strategy_reuse",
        category="intelligence",
        name="Strategy Reuse",
        objective="Research AI agent safety guidelines using the most successful prior strategy pattern.",
        description="Tests learned strategy ranking and reuse.",
        expects_tools=True,
        success_criteria={"min_quality": 0.45, "allow_failure": True},
    ),
    BenchmarkTask(
        id="semantic_retrieval_accuracy",
        category="intelligence",
        name="Semantic Retrieval",
        objective="Find and summarize stored knowledge about SQLite vs PostgreSQL tradeoffs.",
        description="Tests semantic similarity search accuracy.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="contradiction_handling",
        category="intelligence",
        name="Contradiction Handling",
        objective="Report conflicting facts about Ollama deployment and reconcile with confidence scores.",
        description="Tests world model contradiction detection.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="multi_hop_reasoning",
        category="knowledge_reasoning",
        name="Multi-Hop Graph Reasoning",
        objective="Trace relationships between OpenAI, Microsoft, and Azure through the knowledge graph.",
        description="Tests multi-hop entity path discovery.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="contradiction_resolution",
        category="knowledge_reasoning",
        name="Contradiction Resolution",
        objective="Reconcile conflicting claims about SQLite WAL mode vs DELETE mode with evidence scores.",
        description="Tests conflict resolver and belief propagation.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="causal_inference",
        category="knowledge_reasoning",
        name="Causal Inference",
        objective="Explain how high GPU demand causes increased inference latency in local LLM deployments.",
        description="Tests causal link extraction and temporal validation.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="strategic_insight",
        category="knowledge_reasoning",
        name="Strategic Insight Generation",
        objective="Synthesize cross-session patterns into strategic insights for autonomous AI operators.",
        description="Tests knowledge synthesis and strategic insight recording.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="predictive_reasoning",
        category="knowledge_reasoning",
        name="Predictive World State",
        objective="Predict likely entity relationships for Ollama and Docker based on prior knowledge.",
        description="Tests world-state predictor and graph neighborhood expansion.",
        expects_tools=False,
        success_criteria={"min_quality": 0.4, "allow_failure": True},
    ),
    BenchmarkTask(
        id="desktop_navigation",
        category="computer_use",
        name="Desktop Navigation",
        objective="Capture a screenshot of the desktop, detect UI elements, and describe the active window.",
        description="Tests screen perception, OCR, and desktop state tracking.",
        expects_tools=False,
        max_duration_seconds=120,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="visual_grounding",
        category="computer_use",
        name="Visual Grounding",
        objective="Find and click the OK button on screen using visual grounding coordinates.",
        description="Tests coordinate grounding and click confidence scoring.",
        expects_tools=False,
        max_duration_seconds=120,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="file_workflow",
        category="computer_use",
        name="File Workflow",
        objective="Open Notepad, type a short note, and save it to the sandbox workspace.",
        description="Tests file interaction and hybrid desktop workflow.",
        expects_tools=False,
        max_duration_seconds=180,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="multimodal_recovery",
        category="computer_use",
        name="Multimodal Recovery",
        objective="Click a non-existent UI button, recover via reflection, and retry with re-grounding.",
        description="Tests perception-action cycle recovery and interaction reflection.",
        expects_tools=False,
        max_duration_seconds=120,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="ui_reasoning",
        category="computer_use",
        name="UI Reasoning",
        objective="Analyze the current screen layout and list all interactive UI elements with bounding boxes.",
        description="Tests multimodal reasoning loop and visual context injection.",
        expects_tools=False,
        max_duration_seconds=120,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="desktop_recovery",
        category="computer_use",
        name="Desktop Recovery",
        objective="Click a missing UI button, recover via reflection, and retry with re-grounding on the desktop.",
        description="Tests adaptive UI recovery and workflow resumption.",
        expects_tools=False,
        max_duration_seconds=180,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="cross_application_workflow",
        category="computer_use",
        name="Cross-Application Workflow",
        objective="Multi-step workflow: open browser, then open Notepad and type a summary note.",
        description="Tests cross-app orchestration and application switching.",
        expects_tools=False,
        max_duration_seconds=300,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="semantic_ui_adaptation",
        category="computer_use",
        name="Semantic UI Adaptation",
        objective="Analyze the screen layout semantically, detect dialog or form elements, and describe likely next actions.",
        description="Tests semantic UI parsing and interaction prediction.",
        expects_tools=False,
        max_duration_seconds=120,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="persistent_session_restore",
        category="computer_use",
        name="Persistent Session Restore",
        objective="Start a desktop session, capture workspace snapshot, and describe active applications for session restore.",
        description="Tests persistent session layer and workspace snapshots.",
        expects_tools=False,
        max_duration_seconds=120,
        success_criteria={"allow_failure": True},
    ),
    BenchmarkTask(
        id="long_running_desktop_agent",
        category="computer_use",
        name="Long-Running Desktop Agent",
        objective="Long-running workflow: capture screenshot, analyze UI, open Notepad, type a note, then save to workspace.",
        description="Tests task chains, checkpointing, and long-running desktop agent stability.",
        expects_tools=False,
        max_duration_seconds=300,
        success_criteria={"allow_failure": True},
    ),
]


def get_task(task_id: str) -> BenchmarkTask | None:
    return next((t for t in BENCHMARK_TASKS if t.id == task_id), None)


def tasks_by_category(category: str) -> list[BenchmarkTask]:
    return [t for t in BENCHMARK_TASKS if t.category == category]


def all_categories() -> list[str]:
    return sorted({t.category for t in BENCHMARK_TASKS})
