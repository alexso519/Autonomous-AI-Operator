"""
Autonomous Execution Routes — Entry point for autonomous AI operation.

New endpoints for autonomous task execution:
  POST   /api/autonomous/analyze     → Analyze a task (returns TaskAnalysis)
  POST   /api/autonomous/execute     → Execute autonomously (analyze → plan → run)

These sit ON TOP of existing /api/workflows and /api/executions endpoints.
No changes to existing workflow system.

Autonomous flow:
  1. User submits objective
  2. TaskAnalyzer classifies and generates agent list
  3. WorkflowPlanner (future) converts to execution graph
  4. engine.execute_workflow() runs the generated workflow
  5. All events stream via existing EventManager/SSE
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.locale import t
from app.database.database import get_db
from app.execution.manager import execution_manager
from app.execution.autonomous_service import get_autonomous_service
from app.execution.model_preferences import get_model_preference_store
from app.planning import get_planner
from app.services.task_analyzer import get_analyzer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/autonomous", tags=["autonomous"])


# ── Request/Response Models ───────────────────────────────────


class AnalyzeRequest(BaseModel):
    """Request to analyze a task."""

    objective: str = Field(min_length=10, max_length=5000)
    """User's high-level objective"""


class AnalyzeResponse(BaseModel):
    """Response from task analysis."""

    objective: str
    taskType: str
    complexity: str
    riskLevel: str
    requiredAgents: list[dict[str, Any]]
    keywords: list[str]
    estimatedSteps: int
    requiresHumanApproval: bool
    reasoning: str


class ExecuteRequest(BaseModel):
    """Request to execute a task autonomously."""

    objective: str = Field(min_length=10, max_length=5000)
    """User's high-level objective"""

    workflow_name: str | None = Field(None, max_length=200)
    """Optional custom workflow name (auto-generated if not provided)"""
    llm_config: dict[str, Any] | None = Field(
        default=None,
        alias="model_config",
        serialization_alias="model_config",
    )
    """Optional runtime model override for this execution."""


class ExecuteResponse(BaseModel):
    """Response from autonomous execution start."""

    executionId: str
    workflowId: str
    workflowName: str
    status: str
    taskAnalysis: dict[str, Any]
    streamUrl: str
    """URL to connect to SSE stream for real-time updates"""


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/analyze")
async def analyze_task(req: AnalyzeRequest) -> AnalyzeResponse:
    """
    Analyze a task without executing it.

    This endpoint only performs analysis — no workflow is created or executed.
    Useful for previewing what agents and steps will be needed.

    Returns:
        TaskAnalysis with classification, required agents, complexity, etc.

    Raises:
        HTTPException 400 if objective is invalid
        HTTPException 500 if analysis fails
    """
    try:
        analyzer = get_analyzer()
        analysis = await analyzer.analyze(req.objective)

        logger.info(
            "Task analysis complete | type=%s | agents=%d | complexity=%s",
            analysis.task_type.value,
            len(analysis.required_agents),
            analysis.complexity.value,
        )

        return AnalyzeResponse(**analysis.to_dict())

    except ValueError as e:
        logger.warning("Analysis validation failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Task analysis failed: %s", e)
        raise HTTPException(status_code=500, detail=t("analysis_failed", error=str(e)))


@router.post("/execute")
async def execute_task_autonomous(req: ExecuteRequest) -> ExecuteResponse:
    """
    Execute a task autonomously end-to-end.

    This endpoint:
    1. Analyzes the objective
    2. (Future) Plans the workflow automatically
    3. Creates a workflow with generated agents
    4. Starts execution
    5. Returns stream URL for real-time progress

    The execution follows the same pipeline as manual workflows:
    - Execution is tracked in ExecutionManager
    - Events stream via SSE to frontend
    - Steps/approvals are persisted to DB
    - Human approval is requested for risky operations

    Args:
        objective: User's task description
        workflow_name: Optional name (auto-generated if omitted)

    Returns:
        ExecuteResponse with execution ID and stream URL

    Raises:
        HTTPException 400 if objective is invalid
        HTTPException 500 if execution fails to start
    """
    try:
        store = get_model_preference_store()
        provider = (
            str(req.llm_config.get("provider")).strip().lower()
            if req.llm_config and req.llm_config.get("provider") is not None
            else store.get().provider
        )
        if provider not in set(store.get_options()["providers"]):
            raise HTTPException(status_code=400, detail=t("provider_not_allowed"))
        if provider == "tongyi" and not store.get().api_key:
            raise HTTPException(status_code=400, detail=t("api_key_not_configured"))

        if req.llm_config:
            allowed_models = set(store.get_options()["models"])
            model = req.llm_config.get("model")
            if model and model not in allowed_models:
                raise HTTPException(status_code=400, detail=t("model_not_allowed"))
            temperature = req.llm_config.get("temperature")
            if temperature is not None and (not isinstance(temperature, (int, float)) or temperature < 0 or temperature > 2):
                raise HTTPException(status_code=400, detail=t("temperature_range"))

        # 1. Analyze the task
        analyzer = get_analyzer()
        analysis = await analyzer.analyze(req.objective)

        logger.info(
            "Autonomous execution starting | type=%s | agents=%d | complexity=%s",
            analysis.task_type.value,
            len(analysis.required_agents),
            analysis.complexity.value,
        )

        # Delegate orchestration to the AutonomousExecutionService
        service = get_autonomous_service()
        result = await service.start(
            req.objective,
            req.workflow_name,
            model_config=req.llm_config,
        )

        return ExecuteResponse(
            executionId=result["executionId"],
            workflowId=result["workflowId"],
            workflowName=result["workflowName"],
            status=result["status"],
            taskAnalysis=result["taskAnalysis"],
            streamUrl=result["streamUrl"],
        )

    except ValueError as e:
        logger.warning("Autonomous execution rejected: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Autonomous execution failed to start: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=t("execution_failed", error=str(e)))


# ── Health check ──────────────────────────────────────────────


@router.get("/health")
async def health() -> dict[str, str]:
    """Check if autonomous service is ready."""
    return {"status": "ok", "service": "autonomous"}
