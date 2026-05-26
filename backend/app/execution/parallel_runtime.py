"""
Parallel execution of independent agents within a dependency DAG.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from app.execution.adaptive_graph import AdaptiveExecutionGraph

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]
ExecuteStepFn = Callable[..., Awaitable[tuple[str, str | None]]]


class ParallelRuntime:
    """Execute a batch of ready nodes concurrently when the DAG allows it."""

    MAX_CONCURRENT = 3

    @classmethod
    async def execute_ready_batch(
        cls,
        execution_id: str,
        graph: AdaptiveExecutionGraph,
        ready_nodes: list[dict[str, Any]],
        agent_count: int,
        ctx: Any,
        logs: list[dict[str, Any]],
        steps: list[dict[str, Any]],
        execute_step: ExecuteStepFn,
        emit_fn: EmitFn,
        agent_timeout: int,
        check_cancelled: Callable[[str, str], Awaitable[bool]],
        step_lock: asyncio.Lock | None = None,
    ) -> list[tuple[dict[str, Any], str, str | None]]:
        """
        Run ready nodes — parallel when strategy allows and DAG permits.

        Returns list of (node, status, error) tuples in completion order.
        """
        if not ready_nodes:
            return []

        node_ids = [n["id"] for n in ready_nodes]
        parallel_enabled = bool(
            ctx.get_workflow_memory("parallel_execution")
            if hasattr(ctx, "get_workflow_memory")
            else False
        )

        use_parallel = (
            parallel_enabled
            and len(ready_nodes) > 1
            and graph.can_parallelize(node_ids)
        )

        if not use_parallel:
            results: list[tuple[dict[str, Any], str, str | None]] = []
            for idx_offset, node in enumerate(ready_nodes):
                if await check_cancelled(execution_id, node.get("data", {}).get("label", "")):
                    results.append((node, "cancelled", None))
                    break
                status, error = await execute_step(
                    execution_id=execution_id,
                    node=node,
                    idx=len(steps) + idx_offset + 1,
                    agent_count=agent_count,
                    ctx=ctx,
                    logs=logs,
                    steps=steps,
                    agent_timeout=agent_timeout,
                )
                results.append((node, status, error))
                if status != "completed":
                    break
            return results

        labels = [
            n.get("data", {}).get("label", n.get("id", "")) for n in ready_nodes
        ]
        await emit_fn(
            execution_id,
            "parallel_group_started",
            "system",
            f"Running {len(ready_nodes)} agents in parallel: {', '.join(labels)}",
            nodeIds=node_ids,
            agents=labels,
            concurrency=min(len(ready_nodes), cls.MAX_CONCURRENT),
        )

        sem = asyncio.Semaphore(cls.MAX_CONCURRENT)
        base_idx = len(steps)

        async def _run_one(node: dict[str, Any], offset: int) -> tuple[dict[str, Any], str, str | None]:
            async with sem:
                if await check_cancelled(
                    execution_id, node.get("data", {}).get("label", "")
                ):
                    return node, "cancelled", None
                if step_lock is not None:
                    async with step_lock:
                        status, error = await execute_step(
                            execution_id=execution_id,
                            node=node,
                            idx=base_idx + offset + 1,
                            agent_count=agent_count,
                            ctx=ctx,
                            logs=logs,
                            steps=steps,
                            agent_timeout=agent_timeout,
                        )
                else:
                    status, error = await execute_step(
                        execution_id=execution_id,
                        node=node,
                        idx=base_idx + offset + 1,
                        agent_count=agent_count,
                        ctx=ctx,
                        logs=logs,
                        steps=steps,
                        agent_timeout=agent_timeout,
                    )
                return node, status, error

        tasks = [
            _run_one(node, i) for i, node in enumerate(ready_nodes)
        ]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for item in gathered:
            if isinstance(item, Exception):
                logger.error("Parallel agent error: %s", item)
                continue
            results.append(item)

        return results
