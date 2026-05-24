"""
Context manager — passes outputs between sequentially executed agents.

Strategy (intentionally simple):
  - Each agent receives the combined output of ALL previously executed agents.
  - This gives every downstream agent full visibility into the conversation so far.
  - Outputs are accumulated in a dict keyed by node_id.

No complex DAG resolution or selective context passing — just append and forward.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ContextManager:
    """
    Accumulates agent outputs during sequential workflow execution.

    Usage:
        ctx = ContextManager()
        ctx.set_context("initial task description ...")

        # After each agent completes:
        ctx.add_output("node_1", "Research findings ...")

        # Next agent gets all prior context:
        prior = ctx.get_combined_context()  # "initial ... [Researcher]: Research findings ..."
    """

    def __init__(self) -> None:
        self._initial_context: str = ""
        self._outputs: dict[str, str] = {}

    def set_context(self, context: str) -> None:
        """Set the initial workflow-level context (e.g. workflow description)."""
        self._initial_context = context.strip()

    def get_initial_context(self) -> str:
        """Return the initial workflow-level context."""
        return self._initial_context

    def add_output(self, node_id: str, agent_name: str, output: str) -> None:
        """
        Store the output from a completed agent node.

        Args:
            node_id: The canvas node identifier.
            agent_name: Human-readable agent name (for log formatting).
            output: The raw text output from the agent.
        """
        if output and output.strip():
            formatted = f"[{agent_name}]: {output.strip()}"
        else:
            formatted = f"[{agent_name}]: (no output)"
        self._outputs[node_id] = formatted
        logger.debug("Context stored for node %s (%s)", node_id, agent_name)

    def get_combined_context(self) -> str:
        """
        Return all accumulated context as a single string.

        This is what gets passed to each new agent before execution.
        """
        parts: list[str] = []

        if self._initial_context:
            parts.append(f"Workflow Context:\n{self._initial_context}")

        if self._outputs:
            parts.append("\nPrevious Agent Outputs:")
            for node_id in self._outputs:
                parts.append(self._outputs[node_id])

        return "\n\n".join(parts)

    def get_latest_output(self) -> str:
        """
        Return the most recent agent output only.
        Useful for single-step context or debugging.
        """
        if not self._outputs:
            return ""
        last_key = list(self._outputs.keys())[-1]
        return self._outputs[last_key]

    def get_all_outputs(self) -> dict[str, str]:
        """Return all stored outputs keyed by node_id."""
        return dict(self._outputs)

    def reset(self) -> None:
        """Clear all context (for a fresh workflow run)."""
        self._initial_context = ""
        self._outputs = {}
        logger.debug("Context manager reset")