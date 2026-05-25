"""
Execution module — sequential workflow execution engine with
human-in-the-loop approval checkpoints.

Runtime safety layer:
  ExecutionManager  — Centralized task registry, cancellation, thread mgmt
  ExecutionLock     — Per-execution mutual exclusion guard

Engine exports:
  execute_workflow()     — Run a workflow sequentially through all agent nodes
  resume_workflow()      — Resume execution after approval gate
  reject_execution()     — Reject a paused execution

Supporting exports:
  build_agent()          — Create a CrewAI Agent from canvas node data
  ContextManager         — Accumulate and pass context between agents

Runtime safety exports:
  execution_manager      — Singleton ExecutionManager instance
  recover_orphaned_executions — Startup crash recovery
"""

from app.execution.engine import (
    execute_workflow,
    resume_workflow,
    reject_execution,
)
from app.execution.agent_factory import build_agent
from app.execution.context_manager import ContextManager
from app.execution.manager import (
    execution_manager,
    ExecutionManager,
    ExecutionLock,
    ExecutionStatus,
    RegisteredTask,
    recover_orphaned_executions,
)
from app.execution.runtime_state import (
    RuntimeState,
    get_latest_state,
    get_state_history,
    init_execution_state,
    transition_execution_state,
)

__all__ = [
    # Engine
    "execute_workflow",
    "resume_workflow",
    "reject_execution",
    # Agent/context
    "build_agent",
    "ContextManager",
    # Runtime safety
    "execution_manager",
    "ExecutionManager",
    "ExecutionLock",
    "ExecutionStatus",
    "RegisteredTask",
    "recover_orphaned_executions",
    # Runtime state machine
    "RuntimeState",
    "get_latest_state",
    "get_state_history",
    "init_execution_state",
    "transition_execution_state",
]