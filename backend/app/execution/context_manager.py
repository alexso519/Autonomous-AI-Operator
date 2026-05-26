"""
Shared execution context manager.

This manager maintains workflow-level and node-level memory, stores
intermediate agent outputs, and can persist shared state into SQLite.

Context assembly is delegated to the unified ContextAssembler via
get_combined_context() — agents should not manually concatenate blocks.

Memory model:
  - Initial workflow context
  - Workflow-level shared memory
  - Per-node memory / metadata
  - Agent outputs keyed by node_id
  - Approval decisions and execution metadata
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)


class ContextManager:
    """
    Accumulates shared workflow state during sequential execution.

    Provides a deterministic inspection model for replay, debugging,
    and approval-resume execution.
    """

    def __init__(self, execution_id: str | None = None) -> None:
        self.execution_id = execution_id
        self._initial_context: str = ""
        self._workflow_memory: dict[str, Any] = {}
        self._node_memory: dict[str, dict[str, Any]] = {}
        self._outputs: dict[str, dict[str, Any]] = {}
        self._approval_memory: dict[str, Any] = {}
        self._context_budget = None  # lazy-loaded ContextBudget

    def set_context(self, context: str) -> None:
        """Set the initial workflow-level context."""
        self._initial_context = context.strip()

    def get_initial_context(self) -> str:
        """Return the initial workflow-level context."""
        return self._initial_context

    def get_context(self) -> str:
        """Alias for get_initial_context (backward compatibility)."""
        return self.get_initial_context()

    def set_workflow_memory(self, key: str, value: Any) -> None:
        """Store a workflow-level memory item."""
        self._workflow_memory[key] = value
        logger.debug("Workflow memory set: %s", key)

    def get_workflow_memory(self, key: str, default: Any = None) -> Any:
        """Retrieve a workflow-level memory item."""
        return self._workflow_memory.get(key, default)

    def merge_workflow_memory(self, memory: dict[str, Any]) -> None:
        """Merge a batch of workflow memory entries."""
        self._workflow_memory.update(memory)
        logger.debug("Workflow memory merged: %s", list(memory.keys()))

    def add_node_memory(self, node_id: str, key: str, value: Any) -> None:
        """Store metadata for a specific node."""
        node = self._node_memory.setdefault(node_id, {})
        node[key] = value
        logger.debug("Node memory set: %s.%s", node_id, key)

    def get_node_memory(self, node_id: str) -> dict[str, Any]:
        """Return all memory stored for a single node."""
        return dict(self._node_memory.get(node_id, {}))

    def add_output(
        self,
        node_id: str,
        agent_name: str,
        output: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store the output and metadata from a completed agent node."""
        raw_text = output.strip() if output and output.strip() else "(no output)"
        self._outputs[node_id] = {
            "agent_name": agent_name,
            "output": raw_text,
            "metadata": metadata or {},
        }
        self.add_node_memory(node_id, "agent_name", agent_name)
        self.add_node_memory(node_id, "output", raw_text)
        if metadata:
            self.add_node_memory(node_id, "metadata", metadata)
        self.set_workflow_memory("last_agent", agent_name)
        self.set_workflow_memory("last_node_id", node_id)
        logger.debug("Context output stored for node %s (%s)", node_id, agent_name)

    def add_approval_decision(
        self,
        node_id: str,
        decision: str,
        notes: str | None = None,
        decided_by: str = "user",
    ) -> None:
        """Store approval decisions in workflow memory."""
        entry = {
            "decision": decision,
            "notes": notes or "",
            "decided_by": decided_by,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._approval_memory[node_id] = entry
        self.set_workflow_memory("last_approval", {"node_id": node_id, "decision": decision})
        logger.debug("Approval memory stored for node %s: %s", node_id, decision)

    def get_combined_context(
        self,
        model_tier: str = "standard",
        is_synthesis: bool = False,
        is_retry: bool = False,
        agent_name: str = "",
        agent_role: str = "",
        tool_plan_block: str = "",
    ) -> str:
        """Return compressed shared context via unified ContextAssembler."""
        from app.execution.context_assembler import ContextAssembler
        from app.execution.retrieval_planner import AssemblyRequest

        research_mode = bool(self.get_workflow_memory("autonomous_research_mode"))
        confidence = self.get_workflow_memory("last_confidence")
        if isinstance(confidence, dict):
            confidence = confidence.get("score")

        assembler = ContextAssembler.for_context(self, execution_id=self.execution_id or "")
        request = AssemblyRequest(
            model_tier=model_tier,
            is_synthesis=is_synthesis,
            is_retry=is_retry,
            agent_name=agent_name,
            agent_role=agent_role,
            task_complexity=str(self.get_workflow_memory("task_complexity") or "moderate"),
            confidence_score=float(confidence) if confidence is not None else None,
            research_mode=research_mode,
            tool_plan_block=tool_plan_block,
        )
        result = assembler.assemble_sync(request)
        return result.context

    def get_latest_output(self) -> str:
        """Return the most recent agent output only."""
        if not self._outputs:
            return ""
        last_key = list(self._outputs.keys())[-1]
        return self._outputs[last_key]["output"]

    def get_all_outputs(self) -> dict[str, str]:
        """Return raw outputs keyed by node_id."""
        return {node_id: data["output"] for node_id, data in self._outputs.items()}

    def get_state(self) -> dict[str, Any]:
        """Return the full shared memory state for persistence."""
        return {
            "initial_context": self._initial_context,
            "workflow_memory": self._workflow_memory,
            "node_memory": self._node_memory,
            "outputs": self._outputs,
            "approval_memory": self._approval_memory,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore shared memory from a persisted state dictionary."""
        self._initial_context = state.get("initial_context", "")
        self._workflow_memory = state.get("workflow_memory", {}) or {}
        self._node_memory = state.get("node_memory", {}) or {}
        self._outputs = state.get("outputs", {}) or {}
        self._approval_memory = state.get("approval_memory", {}) or {}
        logger.debug("Context manager state restored")

    async def persist(self) -> None:
        """Persist shared memory into the execution record."""
        if not self.execution_id:
            logger.debug("No execution_id available; skipping persistence")
            return

        db = await get_db()
        await db.execute(
            "UPDATE executions SET shared_memory = ? WHERE id = ?",
            (json.dumps(self.get_state(), ensure_ascii=False), self.execution_id),
        )
        await db.commit()
        logger.debug("Shared execution memory persisted for %s", self.execution_id)

    @classmethod
    async def load_from_db(cls, execution_id: str) -> "ContextManager":
        """Load shared execution memory from the executions table."""
        ctx = cls(execution_id=execution_id)
        db = await get_db()
        cursor = await db.execute(
            "SELECT shared_memory FROM executions WHERE id = ?",
            (execution_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return ctx

        raw = dict(row).get("shared_memory")
        if not raw:
            return ctx

        try:
            state = json.loads(raw)
            ctx.restore_state(state)
        except Exception:
            logger.warning(
                "Failed to load shared memory for execution %s", execution_id
            )
        return ctx

    def reset(self) -> None:
        """Clear all shared context state."""
        self._initial_context = ""
        self._workflow_memory = {}
        self._node_memory = {}
        self._outputs = {}
        self._approval_memory = {}
        logger.debug("Context manager reset")
