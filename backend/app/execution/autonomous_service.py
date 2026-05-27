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

    async def start(
        self,
        objective: str,
        workflow_name: str | None = None,
        *,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Analyze, plan, persist workflow and start execution.

        Returns a dict with keys: executionId, workflowId, workflowName,
        status, taskAnalysis, streamUrl
        """
        # 1. Analyze
        analysis = await self._analyzer.analyze(objective)

        # 1b. Production safety rate limit
        from app.execution.production_safety import ProductionSafetyGuard

        active = execution_manager.count_active()
        safety = ProductionSafetyGuard.pre_start_check(active)
        if not safety.allowed:
            raise RuntimeError(f"Cannot start execution: {safety.reason}")

        from app.execution.runtime_hardening import RuntimeHardening

        ok, bp_reason = RuntimeHardening.check_backpressure(active)
        if not ok:
            raise RuntimeError(f"Cannot start execution: {bp_reason}")

        # 1c. Optimize agent spawn count
        from app.execution.agent_spawn_optimizer import AgentSpawnOptimizer

        analysis = AgentSpawnOptimizer.apply_to_analysis(analysis)

        # 2. Deterministic plan from analysis
        nodes, edges = self._planner.plan(analysis)

        from app.execution.research_agents import (
            apply_research_profiles,
            is_research_objective,
        )
        from app.execution.coding_agents import (
            apply_coding_profiles,
            is_computer_use_objective,
        )

        if is_computer_use_objective(objective):
            nodes = apply_coding_profiles(nodes, objective)
        elif is_research_objective(objective):
            nodes = apply_research_profiles(nodes, objective)

        # 2b. Attach tool plans to nodes for observability
        from app.execution.tool_planning import ToolPlanner

        tool_plans = ToolPlanner.plan_workflow_tools(nodes, analysis.objective)
        nodes = [
            ToolPlanner.inject_node_data(n, tool_plans[n["id"]])
            if n.get("id") in tool_plans
            else n
            for n in nodes
        ]
        # Inject task complexity for model routing
        for n in nodes:
            if n.get("type", n.get("data", {}).get("nodeType")) != "approval":
                n.setdefault("data", {})["taskComplexity"] = analysis.complexity.value
                break

        # 2c. Apply user-selected model settings (server-validated in routes).
        forced_model = None
        forced_provider = None
        forced_temperature = None
        forced_max_tokens = None
        if model_config:
            maybe_provider = model_config.get("provider")
            maybe_model = model_config.get("model")
            maybe_temp = model_config.get("temperature")
            maybe_max_tokens = model_config.get("maxTokens")
            if isinstance(maybe_provider, str) and maybe_provider.strip():
                forced_provider = maybe_provider.strip().lower()
            if isinstance(maybe_model, str) and maybe_model.strip():
                forced_model = maybe_model.strip()
            if isinstance(maybe_temp, (int, float)):
                forced_temperature = float(maybe_temp)
            if isinstance(maybe_max_tokens, (int, float)):
                forced_max_tokens = int(maybe_max_tokens)

        if forced_provider or forced_model or forced_temperature is not None or forced_max_tokens is not None:
            for n in nodes:
                node_type = n.get("type", n.get("data", {}).get("nodeType"))
                if node_type == "approval":
                    continue
                data = n.setdefault("data", {})
                if forced_provider:
                    data["forcedProvider"] = forced_provider
                if forced_model:
                    # Read by build_agent_routed to bypass adaptive router.
                    data["forcedModel"] = forced_model
                if forced_temperature is not None:
                    data["temperature"] = forced_temperature
                if forced_max_tokens is not None:
                    data["maxTokens"] = forced_max_tokens

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

        spawn_plan = AgentSpawnOptimizer.optimize(analysis)
        await AgentSpawnOptimizer.persist_spawn_plan(
            execution_id, spawn_plan, analysis.task_type.value
        )

        # 5. Register and launch execution through the manager.
        # register() is async — it acquires the per-execution lock, wraps the
        # coroutine in _wrap_execution (SSE + cleanup guarantees), and creates
        # the asyncio.Task.  Passing the raw coroutine (not a pre-created task)
        # is required so the manager owns the full lifecycle.
        from app.execution.engine import execute_workflow

        task = await execution_manager.register(
            execution_id=execution_id,
            workflow_id=workflow_id,
            coro=execute_workflow(
                execution_id=execution_id,
                workflow_id=workflow_id,
                nodes=nodes,
                edges=edges,
                workflow_name=workflow_name,
                workflow_description=analysis.objective,
            ),
            owner_id="autonomous",
        )

        if task is None:
            raise RuntimeError(
                f"Could not start execution {execution_id}: lock contention"
            )

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
