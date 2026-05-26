"""
Long-execution stability: heartbeats, stall detection, checkpoint snapshots.

Single-process architecture — no distributed workers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Stall threshold: no heartbeat for this many seconds → stalled
STALL_THRESHOLD_SECONDS = 120
# Heartbeat emit interval during agent execution
HEARTBEAT_INTERVAL_SECONDS = 30
# Graceful recovery timeout extension (seconds)
RECOVERY_TIMEOUT_EXTENSION = 60


@dataclass
class ExecutionHeartbeat:
    execution_id: str
    node_id: str
    agent_name: str
    timestamp: str
    phase: str = "running"


@dataclass
class StallStatus:
    is_stalled: bool
    seconds_since_heartbeat: float
    last_agent: str | None
    last_node_id: str | None
    recovery_attempted: bool = False


class ExecutionStabilityMonitor:
    """Track execution liveness and detect stalled agents."""

    _heartbeats: dict[str, ExecutionHeartbeat] = {}
    _monitor_tasks: dict[str, asyncio.Task[None]] = {}
    _recovery_counts: dict[str, int] = {}

    @classmethod
    def record_heartbeat(
        cls,
        execution_id: str,
        node_id: str,
        agent_name: str,
        phase: str = "running",
    ) -> ExecutionHeartbeat:
        hb = ExecutionHeartbeat(
            execution_id=execution_id,
            node_id=node_id,
            agent_name=agent_name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            phase=phase,
        )
        cls._heartbeats[execution_id] = hb
        return hb

    @classmethod
    def get_last_heartbeat(cls, execution_id: str) -> ExecutionHeartbeat | None:
        return cls._heartbeats.get(execution_id)

    @classmethod
    def check_stalled(cls, execution_id: str) -> StallStatus:
        hb = cls._heartbeats.get(execution_id)
        if hb is None:
            return StallStatus(
                is_stalled=False,
                seconds_since_heartbeat=0.0,
                last_agent=None,
                last_node_id=None,
            )

        last_ts = datetime.fromisoformat(hb.timestamp.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
        return StallStatus(
            is_stalled=elapsed >= STALL_THRESHOLD_SECONDS,
            seconds_since_heartbeat=elapsed,
            last_agent=hb.agent_name,
            last_node_id=hb.node_id,
        )

    @classmethod
    async def emit_heartbeat_event(
        cls,
        execution_id: str,
        emit_fn: Any,
        node_id: str,
        agent_name: str,
        phase: str = "running",
    ) -> None:
        """Record heartbeat and emit SSE event."""
        hb = cls.record_heartbeat(execution_id, node_id, agent_name, phase)
        await emit_fn(
            execution_id,
            "execution_heartbeat",
            agent_name,
            f"Heartbeat: {agent_name} still active ({phase})",
            nodeId=node_id,
            phase=phase,
            timestamp=hb.timestamp,
        )

    @classmethod
    async def start_agent_monitor(
        cls,
        execution_id: str,
        node_id: str,
        agent_name: str,
        emit_fn: Any,
        cancel_check: Any,
    ) -> asyncio.Task[None]:
        """Background task that emits heartbeats and detects stalls during agent run."""

        async def _monitor() -> None:
            cls.record_heartbeat(execution_id, node_id, agent_name, "thinking")
            stall_warned = False

            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)

                if cancel_check(execution_id):
                    break

                await cls.emit_heartbeat_event(
                    execution_id, emit_fn, node_id, agent_name, "running"
                )

                stall = cls.check_stalled(execution_id)
                if stall.is_stalled and not stall_warned:
                    stall_warned = True
                    await emit_fn(
                        execution_id,
                        "execution_stalled",
                        agent_name,
                        f"Agent {agent_name} may be stalled ({stall.seconds_since_heartbeat:.0f}s idle)",
                        nodeId=node_id,
                        secondsIdle=stall.seconds_since_heartbeat,
                        recoveryAvailable=True,
                    )

        task = asyncio.create_task(_monitor(), name=f"hb-{execution_id[:8]}")
        cls._monitor_tasks[execution_id] = task
        return task

    @classmethod
    async def stop_agent_monitor(cls, execution_id: str) -> None:
        task = cls._monitor_tasks.pop(execution_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @classmethod
    async def save_checkpoint_snapshot(
        cls,
        execution_id: str,
        ctx_state: dict[str, Any],
        sorted_nodes: list[dict[str, Any]],
        current_idx: int,
        steps: list[dict[str, Any]],
        logs: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        workflow_meta: dict[str, Any],
    ) -> str:
        """Persist a lightweight checkpoint snapshot for recovery observability."""
        snapshot_id = uuid.uuid4().hex[:12]
        snapshot = {
            "snapshotId": snapshot_id,
            "executionId": execution_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "currentIdx": current_idx,
            "contextState": ctx_state,
            "nodeCount": len(sorted_nodes),
            "stepCount": len(steps),
            "workflowMeta": workflow_meta,
            "nodes": [{"id": n.get("id"), "label": n.get("data", {}).get("label")} for n in sorted_nodes],
            "edges": edges,
            "recentSteps": steps[-5:],
            "recentLogs": logs[-10:],
        }

        try:
            from app.database.database import get_db

            db = await get_db()
            await db.execute(
                """INSERT INTO execution_checkpoints
                   (id, execution_id, snapshot_data, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    snapshot_id,
                    execution_id,
                    json.dumps(snapshot),
                    snapshot["timestamp"],
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Failed to persist checkpoint snapshot: %s", exc)

        return snapshot_id

    @classmethod
    async def handle_stall_recovery(
        cls,
        execution_id: str,
        emit_fn: Any,
        stall: StallStatus,
    ) -> bool:
        """
        Attempt graceful stall recovery.

        Returns True if recovery was initiated (caller may extend timeout).
        """
        recovery_key = execution_id
        count = getattr(cls, "_recovery_counts", {}).get(recovery_key, 0)
        if not hasattr(cls, "_recovery_counts"):
            cls._recovery_counts = {}
        if count >= 2:
            return False

        cls._recovery_counts[recovery_key] = count + 1

        await emit_fn(
            execution_id,
            "execution_recovery",
            stall.last_agent or "system",
            f"Attempting graceful recovery for stalled agent {stall.last_agent}",
            nodeId=stall.last_node_id,
            recoveryAttempt=count + 1,
            timeoutExtension=RECOVERY_TIMEOUT_EXTENSION,
        )
        return True

    @classmethod
    def cleanup_execution(cls, execution_id: str) -> None:
        cls._heartbeats.pop(execution_id, None)
        if hasattr(cls, "_recovery_counts"):
            cls._recovery_counts.pop(execution_id, None)
        task = cls._monitor_tasks.pop(execution_id, None)
        if task and not task.done():
            task.cancel()


async def cleanup_orphan_executions_on_startup() -> int:
    """
    Supplement recover_orphaned_executions with heartbeat table cleanup.
    Called from main.py startup.
    """
    from app.database.database import get_db

    try:
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """SELECT id FROM executions
               WHERE status IN ('running', 'waiting_approval')
               AND started_at < datetime('now', '-1 hour')"""
        )
        rows = await cursor.fetchall()
        count = 0
        for row in rows:
            await db.execute(
                """UPDATE executions
                   SET status = 'orphaned', completed_at = ?, error = ?
                   WHERE id = ?""",
                (now, "Stale execution cleaned up on startup", row["id"]),
            )
            count += 1
        if count:
            await db.commit()
        return count
    except Exception as exc:
        logger.warning("Orphan cleanup failed: %s", exc)
        return 0
