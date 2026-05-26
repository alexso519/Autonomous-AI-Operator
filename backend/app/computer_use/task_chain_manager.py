"""
Task chain manager — resumable checkpointed task chains.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskChain:
    chain_id: str
    execution_id: str
    objective: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    current_index: int = 0
    status: str = "running"
    branches: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chainId": self.chain_id,
            "executionId": self.execution_id,
            "objective": self.objective[:200],
            "stepCount": len(self.steps),
            "currentIndex": self.current_index,
            "status": self.status,
            "branches": self.branches,
        }


class TaskChainManager:
    """Manage resumable task chains with DB checkpoints."""

    _chains: dict[str, TaskChain] = {}
    _checkpoints: dict[str, list[dict[str, Any]]] = {}

    def __init__(self, execution_id: str, chain_id: str) -> None:
        self.execution_id = execution_id
        self.chain_id = chain_id

    async def start(
        self,
        objective: str,
        steps: list[dict[str, Any]],
        *,
        emit_fn: EmitFn | None = None,
    ) -> TaskChain:
        chain = TaskChain(
            chain_id=self.chain_id,
            execution_id=self.execution_id,
            objective=objective,
            steps=steps,
        )
        self._chains[self.chain_id] = chain
        return chain

    async def resume_index(self) -> int:
        checkpoint = await self._load_latest_checkpoint()
        if checkpoint:
            idx = checkpoint.get("stepIndex", 0)
            return idx + 1 if checkpoint.get("completed") else idx
        chain = self._chains.get(self.chain_id)
        return chain.current_index if chain else 0

    async def complete_step(self, index: int, result: dict[str, Any]) -> None:
        chain = self._chains.get(self.chain_id)
        if chain:
            chain.current_index = index + 1

    async def checkpoint(self, step_index: int, data: dict[str, Any]) -> None:
        checkpoint_data = {"stepIndex": step_index, "completed": True, **data}
        self._checkpoints.setdefault(self.chain_id, []).append(checkpoint_data)
        try:
            from app.database.database import get_db

            db = await get_db()
            cp_id = f"dcp-{uuid.uuid4().hex[:10]}"
            checkpoint_data = {"stepIndex": step_index, "completed": True, **data}
            await db.execute(
                """INSERT INTO desktop_checkpoints
                   (id, execution_id, session_id, checkpoint_label, chain_id,
                    step_index, checkpoint_data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cp_id,
                    self.execution_id,
                    "",
                    f"step_{step_index}",
                    self.chain_id,
                    step_index,
                    json.dumps(checkpoint_data),
                    _now(),
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.debug("Checkpoint persist skipped: %s", exc)

    async def create_branch(
        self,
        step_index: int,
        reason: str,
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any] | None:
        chain = self._chains.get(self.chain_id)
        if not chain:
            return None
        branch = {
            "branchId": f"br-{uuid.uuid4().hex[:8]}",
            "fromStep": step_index,
            "reason": reason[:200],
        }
        chain.branches.append(branch)
        chain.status = "branched"
        if emit_fn:
            await emit_fn(
                self.execution_id,
                "workflow_branch_created",
                "WorkflowExecutor",
                f"Branch at step {step_index}: {reason[:60]}",
                chainId=self.chain_id,
                branch=branch,
            )
        return branch

    async def resume(self, *, emit_fn: EmitFn | None = None) -> int:
        idx = await self.resume_index()
        chain = self._chains.get(self.chain_id)
        if chain:
            chain.status = "running"
        if emit_fn:
            await emit_fn(
                self.execution_id,
                "workflow_resumed",
                "WorkflowExecutor",
                f"Resumed chain at step {idx}",
                chainId=self.chain_id,
                resumeFrom=idx,
            )
        return idx

    async def _load_latest_checkpoint(self) -> dict[str, Any] | None:
        memory = self._checkpoints.get(self.chain_id, [])
        if memory:
            return memory[-1]
        try:
            from app.database.database import get_db

            db = await get_db()
            cursor = await db.execute(
                """SELECT checkpoint_data, step_index
                   FROM desktop_checkpoints
                   WHERE execution_id = ? AND chain_id = ?
                   ORDER BY step_index DESC LIMIT 1""",
                (self.execution_id, self.chain_id),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            data = json.loads(row[0] or "{}")
            data["stepIndex"] = row[1]
            return data
        except Exception as exc:
            logger.debug("Checkpoint load skipped: %s", exc)
            return None

    @classmethod
    def cleanup(cls, chain_id: str) -> None:
        cls._chains.pop(chain_id, None)
        cls._checkpoints.pop(chain_id, None)
