"""
Hierarchical memory tiers for adaptive autonomous execution.

Tiers:
  - working: hot context for the current agent step
  - execution: per-run artifacts (outputs, reflections, strategies)
  - long_term: compressed summaries persisted across steps
  - tool_results: indexed tool call outputs for grounding
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.execution.context_manager import ContextManager

logger = logging.getLogger(__name__)

WORKING_KEY = "hier_working"
EXECUTION_KEY = "hier_execution"
LONG_TERM_KEY = "hier_long_term"
TOOL_RESULTS_KEY = "hier_tool_results"


class HierarchicalMemory:
    """Structured memory layers backed by ContextManager workflow memory."""

    def __init__(self, ctx: ContextManager) -> None:
        self._ctx = ctx
        self._ensure_tiers()

    def _ensure_tiers(self) -> None:
        for key, default in (
            (WORKING_KEY, {}),
            (EXECUTION_KEY, {"outputs": {}, "strategies": [], "replan_events": []}),
            (LONG_TERM_KEY, {"summaries": []}),
            (TOOL_RESULTS_KEY, {"calls": []}),
        ):
            if self._ctx.get_workflow_memory(key) is None:
                self._ctx.set_workflow_memory(key, default)

    def set_working(self, key: str, value: Any) -> None:
        working = dict(self._ctx.get_workflow_memory(WORKING_KEY) or {})
        working[key] = value
        working["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._ctx.set_workflow_memory(WORKING_KEY, working)

    def get_working(self, key: str, default: Any = None) -> Any:
        working = self._ctx.get_workflow_memory(WORKING_KEY) or {}
        return working.get(key, default)

    def clear_working(self) -> None:
        self._ctx.set_workflow_memory(WORKING_KEY, {})

    def record_execution(
        self,
        node_id: str,
        agent_name: str,
        output: str,
        confidence: float | None = None,
        strategy_id: str | None = None,
    ) -> None:
        execution = dict(self._ctx.get_workflow_memory(EXECUTION_KEY) or {})
        outputs = dict(execution.get("outputs") or {})
        outputs[node_id] = {
            "agentName": agent_name,
            "output": output[:8000],
            "confidence": confidence,
            "strategyId": strategy_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        execution["outputs"] = outputs
        execution["last_node_id"] = node_id
        self._ctx.set_workflow_memory(EXECUTION_KEY, execution)

    def append_replan_event(self, event: dict[str, Any]) -> None:
        execution = dict(self._ctx.get_workflow_memory(EXECUTION_KEY) or {})
        events = list(execution.get("replan_events") or [])
        events.append(event)
        execution["replan_events"] = events[-50:]
        self._ctx.set_workflow_memory(EXECUTION_KEY, execution)

    def record_strategy(self, strategy: dict[str, Any]) -> None:
        execution = dict(self._ctx.get_workflow_memory(EXECUTION_KEY) or {})
        strategies = list(execution.get("strategies") or [])
        strategies.append(strategy)
        execution["strategies"] = strategies[-20:]
        execution["active_strategy_id"] = strategy.get("id")
        self._ctx.set_workflow_memory(EXECUTION_KEY, execution)

    def summarize_to_long_term(self, label: str, text: str, max_chars: int = 600) -> None:
        if not text.strip():
            return
        long_term = dict(self._ctx.get_workflow_memory(LONG_TERM_KEY) or {})
        summaries = list(long_term.get("summaries") or [])
        snippet = text.strip()[:max_chars]
        if len(text) > max_chars:
            snippet += "..."
        summaries.append(
            {
                "label": label,
                "text": snippet,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        long_term["summaries"] = summaries[-30:]
        self._ctx.set_workflow_memory(LONG_TERM_KEY, long_term)

    def record_tool_result(
        self,
        tool_name: str,
        invocation_id: str,
        output: Any,
        agent_name: str,
        node_id: str,
    ) -> None:
        tool_mem = dict(self._ctx.get_workflow_memory(TOOL_RESULTS_KEY) or {})
        calls = list(tool_mem.get("calls") or [])
        calls.append(
            {
                "toolName": tool_name,
                "invocationId": invocation_id,
                "agentName": agent_name,
                "nodeId": node_id,
                "output": output if isinstance(output, (dict, list)) else str(output)[:4000],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        tool_mem["calls"] = calls[-100:]
        self._ctx.set_workflow_memory(TOOL_RESULTS_KEY, tool_mem)

    def get_context_block(self, max_summaries: int = 5) -> str:
        """Build a compact block for agent prompts from all tiers."""
        parts: list[str] = []
        working = self._ctx.get_workflow_memory(WORKING_KEY) or {}
        focus = working.get("focus")
        if focus:
            parts.append(f"Current focus: {focus}")

        long_term = self._ctx.get_workflow_memory(LONG_TERM_KEY) or {}
        for item in (long_term.get("summaries") or [])[-max_summaries:]:
            parts.append(f"[{item.get('label', 'summary')}] {item.get('text', '')}")

        tool_mem = self._ctx.get_workflow_memory(TOOL_RESULTS_KEY) or {}
        recent_tools = (tool_mem.get("calls") or [])[-3:]
        for call in recent_tools:
            parts.append(
                f"Tool {call.get('toolName')}: "
                f"{json.dumps(call.get('output', ''), ensure_ascii=False)[:300]}"
            )
        return "\n".join(parts).strip()

    def sync_from_context(self) -> None:
        """Mirror latest tool_calls from workflow memory into tool_results tier."""
        tool_calls = self._ctx.get_workflow_memory("tool_calls") or []
        if not isinstance(tool_calls, list):
            return
        for call in tool_calls[-10:]:
            if call.get("status") != "completed":
                continue
            self.record_tool_result(
                tool_name=call.get("tool_name", "unknown"),
                invocation_id=call.get("id", call.get("invocation_id", "")),
                output=call.get("output"),
                agent_name=call.get("agent_name", "system"),
                node_id=call.get("node_id", ""),
            )
