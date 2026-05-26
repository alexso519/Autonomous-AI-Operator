"""
Dynamic task decomposition and autonomous replanning without workflow restart.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Awaitable, Callable

from app.execution.adaptive_graph import AdaptiveExecutionGraph, GraphMutation
from app.execution.context_manager import ContextManager
from app.execution.hierarchical_memory import HierarchicalMemory
from app.execution.reflection_service import ReflectionFinding, ReplanningService
from app.execution.strategy_selector import ExecutionStrategy, StrategySelector

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class DynamicPlanner:
    """Runtime planner: strategies, decomposition, and adaptive replanning."""

    SUBGOAL_PATTERN = re.compile(
        r"(?i)(?:step\s*\d+|phase\s*\d+|first|second|then|also|additionally)[:\s]+([^.!\n]{20,120})"
    )

    @classmethod
    async def initialize_execution(
        cls,
        execution_id: str,
        objective: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        ctx: ContextManager,
        emit_fn: EmitFn,
        *,
        resume: bool = False,
    ) -> tuple[AdaptiveExecutionGraph, ExecutionStrategy]:
        graph = AdaptiveExecutionGraph(nodes, edges)
        memory = HierarchicalMemory(ctx)
        memory.set_working("objective", objective)

        existing = ctx.get_workflow_memory("selected_strategy")
        if resume and existing:
            strategy = ExecutionStrategy(
                id=existing.get("id", "resumed"),
                label=existing.get("label", "Resumed"),
                approach=existing.get("approach", ""),
                quality_estimate=existing.get("qualityEstimate", 0.7),
                tool_availability=existing.get("toolAvailability", 0.5),
                cost_estimate=existing.get("costEstimate", 0.5),
                hallucination_risk=existing.get("hallucinationRisk", 0.3),
                parallel_friendly=existing.get("parallelFriendly", False),
            )
            ctx.set_workflow_memory("adaptive_graph_enabled", True)
            return graph, strategy

        complexity = str(ctx.get_workflow_memory("task_complexity") or "moderate")

        try:
            from app.intelligence.knowledge_planner import KnowledgePlanner

            kr_plan = await KnowledgePlanner.plan_with_knowledge(
                objective,
                execution_id=execution_id,
                ctx_data=ctx.get_workflow_memory("cognitive_deliberation") or {},
                emit_fn=emit_fn,
            )
            ctx.set_workflow_memory("knowledge_planning", kr_plan)
            block = kr_plan.get("contextBlock") or ""
            if block:
                ctx.set_context((ctx.get_context() or "") + f"\n\n{block}")
        except Exception as exc:
            logger.debug("Knowledge planning skipped: %s", exc)

        candidates = StrategySelector.generate_candidates(objective, nodes, complexity)
        strategy = StrategySelector.select_best(candidates, ctx, objective=objective)
        memory.record_strategy(strategy.to_dict())
        ctx.set_workflow_memory("selected_strategy", strategy.to_dict())
        ctx.set_workflow_memory("strategy_candidates", [c.to_dict() for c in candidates])
        ctx.set_workflow_memory("adaptive_graph_enabled", True)
        ctx.set_workflow_memory("parallel_execution", strategy.parallel_friendly)

        await emit_fn(
            execution_id,
            "strategy_selected",
            "system",
            f"Selected strategy: {strategy.label} (score {strategy.composite_score:.0%})",
            strategyId=strategy.id,
            strategyLabel=strategy.label,
            approach=strategy.approach,
            compositeScore=strategy.composite_score,
            candidates=[c.to_dict() for c in candidates],
            parallelFriendly=strategy.parallel_friendly,
        )

        decomposed = cls._decompose_objective(objective, nodes, graph, ctx)
        if decomposed:
            mutation = graph.mutations[-1] if graph.mutations else None
            await emit_fn(
                execution_id,
                "graph_mutated",
                "system",
                f"Decomposed task into {len(decomposed)} subgoal node(s)",
                mutation=mutation.to_dict() if mutation else {},
                nodeIds=[n["id"] for n in decomposed],
                action="decompose",
            )

        return graph, strategy

    @classmethod
    def _decompose_objective(
        cls,
        objective: str,
        nodes: list[dict[str, Any]],
        graph: AdaptiveExecutionGraph,
        ctx: ContextManager,
    ) -> list[dict[str, Any]]:
        """
        Split complex objectives into child agents when the graph is small
        and the objective implies multiple phases.
        """
        agent_nodes = [n for n in nodes if n.get("type") != "approval"]
        if len(agent_nodes) >= 4:
            return []

        subgoals = cls._extract_subgoals(objective)
        if len(subgoals) < 2:
            return []

        parent = agent_nodes[-1] if agent_nodes else None
        if not parent:
            return []

        spawned: list[dict[str, Any]] = []
        for i, subgoal in enumerate(subgoals[:3]):
            child = graph.spawn_child_agent(parent, subgoal, suffix=str(i))
            spawned.append(child)

        ctx.set_workflow_memory("decomposed_subgoals", [n["id"] for n in spawned])
        HierarchicalMemory(ctx).append_replan_event(
            {"action": "decompose", "subgoals": len(spawned)}
        )
        return spawned

    @classmethod
    def _extract_subgoals(cls, objective: str) -> list[str]:
        found: list[str] = []
        for match in cls.SUBGOAL_PATTERN.finditer(objective):
            text = match.group(1).strip()
            if len(text) > 15:
                found.append(text)
        if len(found) >= 2:
            return found

        if " and " in objective.lower() and len(objective) > 60:
            parts = re.split(r"\s+and\s+", objective, maxsplit=2, flags=re.IGNORECASE)
            if len(parts) >= 2:
                return [p.strip() for p in parts if len(p.strip()) > 20][:3]
        return []

    @classmethod
    async def replan_remaining(
        cls,
        execution_id: str,
        graph: AdaptiveExecutionGraph,
        node: dict[str, Any],
        finding: ReflectionFinding,
        sorted_nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        node_index: int,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        """
        Rebuild remaining plan by injecting recovery nodes — preserves completed work.
        """
        await emit_fn(
            execution_id,
            "replanning_started",
            "system",
            f"Replanning after {finding.agent_name}: {finding.reason}",
            nodeId=finding.node_id,
            issues=finding.issues,
            qualityScore=finding.quality_score,
            retryType=finding.retry_type,
        )

        memory = HierarchicalMemory(ctx)
        memory.append_replan_event(
            {
                "nodeId": finding.node_id,
                "action": finding.action,
                "issues": finding.issues,
            }
        )

        objective = str(ctx.get_workflow_memory("objective") or ctx.get_context() or "")
        try:
            from app.intelligence.intelligence_orchestrator import IntelligenceOrchestrator

            await IntelligenceOrchestrator.retrieve_for_replanning(
                execution_id,
                objective,
                finding.reason or "",
                emit_fn,
            )
        except Exception as exc:
            logger.debug("Semantic replanning retrieval skipped: %s", exc)

        updated = ReplanningService.insert_recovery_steps(
            sorted_nodes=sorted_nodes,
            edges=edges,
            node_index=node_index,
            node=node,
            finding=finding,
            ctx=ctx,
        )

        new_node_ids = [
            n["id"] for n in updated
            if n["id"] not in graph.nodes
        ]
        new_nodes = [n for n in updated if n["id"] in new_node_ids]
        if new_nodes:
            mutation = graph.insert_nodes(
                new_nodes,
                after_node_id=finding.node_id,
                reason="recovery_replan",
            )
            await emit_fn(
                execution_id,
                "graph_mutated",
                "system",
                f"Graph updated: {len(new_nodes)} recovery node(s) inserted",
                mutation=mutation.to_dict(),
                nodeIds=new_node_ids,
                action="recovery_insert",
            )

        await emit_fn(
            execution_id,
            "replanning_completed",
            "system",
            f"Replanning complete — {len(updated)} nodes in execution plan",
            nodeId=finding.node_id,
            totalNodes=len(updated),
            injectedNodes=new_node_ids,
        )

        return updated

    @classmethod
    async def maybe_spawn_subgoal_agents(
        cls,
        execution_id: str,
        graph: AdaptiveExecutionGraph,
        node: dict[str, Any],
        output: str,
        ctx: ContextManager,
        emit_fn: EmitFn,
    ) -> list[dict[str, Any]]:
        """Runtime decomposition when agent output lists explicit follow-up subgoals."""
        if node.get("data", {}).get("executionMetadata", {}).get("subgoal"):
            return []

        subgoals = cls._extract_subgoals(output)
        if len(subgoals) < 2:
            return []

        spawned: list[dict[str, Any]] = []
        for i, subgoal in enumerate(subgoals[:2]):
            child = graph.spawn_child_agent(node, subgoal, suffix=f"rt{i}")
            spawned.append(child)
            await emit_fn(
                execution_id,
                "child_agent_spawned",
                "system",
                f"Spawned child agent for subgoal: {subgoal[:80]}",
                parentNodeId=node.get("id"),
                childNodeId=child["id"],
                subgoal=subgoal,
            )
        return spawned
