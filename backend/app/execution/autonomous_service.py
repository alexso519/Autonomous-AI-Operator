"""Autonomous execution orchestration service.

This service sits above the existing execution engine and orchestrates:
 - TaskAnalyzer
 - DynamicAgentFactory / planner
 - Workflow creation (DB)
 - Execution record creation
 - Launching `execute_workflow` and registering with ExecutionManager

It intentionally reuses the existing engine, streaming and persistence
layers without modifying them.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db
from app.execution.manager import execution_manager
from app.planning import get_planner
from app.services.task_analyzer import get_analyzer

logger = logging.getLogger(__name__)


class AutonomousExecutionService:
    """Service to start autonomous task execution.

    Usage:
        service = AutonomousExecutionService()
        result = await service.start(objective, workflow_name=None)
    """

    def __init__(self) -> None:
        self._planner = get_planner()
        self._analyzer = get_analyzer()

    async def start(self, objective: str, workflow_name: str | None = None) -> dict[str, Any]:
        """Analyze, plan, persist workflow and start execution.

        Returns a dict with keys: executionId, workflowId, workflowName,
        status, taskAnalysis, streamUrl
        """
        # 1. Analyze
        analysis = await self._analyzer.analyze(objective)

        # 2. Deterministic plan from analysis
        nodes, edges = self._planner.plan(analysis)

        # 3. Persist workflow
        workflow_id = str(uuid.uuid4())
        workflow_name = workflow_name or f"Auto: {analysis.task_type.value}"
        now = datetime.now(timezone.utc).isoformat()

        db = await get_db()

        await db.execute(
            """INSERT INTO workflows (id, name, description, nodes, edges, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                workflow_id,
                workflow_name,
                f"Auto-generated: {analysis.objective[:200]}",
                json.dumps(nodes),
                json.dumps(edges),
                now,
                now,
            ),
        )
        await db.commit()

        # 4. Create execution record
        execution_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO executions (id, workflow_id, status, started_at, workflow_name)
               VALUES (?, ?, 'pending', ?, ?)""",
            (execution_id, workflow_id, now, workflow_name),
        )
        await db.commit()

        # 5. Launch engine execution task
        # Import here to avoid circular imports
        from app.execution.engine import execute_workflow

        task = asyncio.create_task(
            execute_workflow(
                execution_id=execution_id,
                workflow_id=workflow_id,
                nodes=nodes,
                edges=edges,
                workflow_name=workflow_name,
                workflow_description=analysis.objective,
            )
        )

        execution_manager.register(execution_id, workflow_id, task)

        logger.info("Autonomous execution started | exec=%s workflow=%s", execution_id, workflow_id)

        return {
            "executionId": execution_id,
            "workflowId": workflow_id,
            "workflowName": workflow_name,
            "status": "running",
            "taskAnalysis": analysis.to_dict(),
            "streamUrl": f"/api/executions/{execution_id}/stream",
        }


_service_instance: AutonomousExecutionService | None = None


def get_autonomous_service() -> AutonomousExecutionService:
    global _service_instance
    if _service_instance is None:
        _service_instance = AutonomousExecutionService()
    return _service_instance
