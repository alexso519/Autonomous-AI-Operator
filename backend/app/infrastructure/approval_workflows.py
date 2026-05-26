"""
Human approval pipelines for restricted runtime actions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_infrastructure_event

logger = logging.getLogger(__name__)

APPROVAL_REQUIRED_CAPABILITIES = frozenset(
    {"shell", "file_delete", "external_api", "computer_use_destructive", "payment"}
)


class ApprovalWorkflows:
    """Manages approval-required action pipelines."""

    @staticmethod
    def enabled() -> bool:
        return settings.enable_approval_workflows or settings.enable_policy_engine

    @staticmethod
    def requires_approval(capability: str, action_type: str = "") -> bool:
        if not ApprovalWorkflows.enabled():
            return False
        return capability in APPROVAL_REQUIRED_CAPABILITIES or action_type in (
            "destructive",
            "financial_transfer",
        )

    @staticmethod
    async def request_approval(
        execution_id: str,
        action_type: str,
        *,
        capability: str = "",
        notes: str = "",
        requested_by: str = "system",
    ) -> dict[str, Any]:
        if not ApprovalWorkflows.requires_approval(capability, action_type):
            return {"required": False, "approved": True}
        try:
            rid = f"apr_{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            db = await get_db()
            await db.execute(
                """INSERT INTO approval_requests
                   (id, execution_id, action_type, capability, status,
                    requested_by, notes, created_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
                (rid, execution_id, action_type, capability, requested_by, notes, now),
            )
            await db.commit()
            await emit_infrastructure_event(
                execution_id,
                "approval_requested",
                f"Approval required for {action_type}",
                requestId=rid,
                capability=capability,
            )
            return {"required": True, "approved": False, "requestId": rid, "status": "pending"}
        except Exception as exc:
            logger.warning("Approval request degraded (auto-deny): %s", exc)
            return {"required": True, "approved": False, "degraded": True}

    @staticmethod
    async def decide(
        request_id: str,
        *,
        granted: bool,
        decided_by: str = "user",
        notes: str = "",
    ) -> dict[str, Any] | None:
        try:
            now = datetime.now(timezone.utc).isoformat()
            status = "granted" if granted else "denied"
            db = await get_db()
            cursor = await db.execute(
                "SELECT execution_id FROM approval_requests WHERE id = ?", (request_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            await db.execute(
                """UPDATE approval_requests SET status = ?, decided_by = ?,
                   notes = ?, decided_at = ? WHERE id = ?""",
                (status, decided_by, notes, now, request_id),
            )
            await db.commit()
            event = "approval_granted" if granted else "approval_denied"
            await emit_infrastructure_event(
                row["execution_id"],
                event,
                f"Approval {status}",
                requestId=request_id,
            )
            return {"requestId": request_id, "status": status}
        except Exception as exc:
            logger.warning("Approval decide failed: %s", exc)
            return None

    @staticmethod
    async def list_pending(*, limit: int = 50) -> list[dict[str, Any]]:
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT * FROM approval_requests WHERE status = 'pending'
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "requestId": r["id"],
                    "executionId": r["execution_id"],
                    "actionType": r["action_type"],
                    "capability": r["capability"],
                    "status": r["status"],
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.debug("List pending approvals skipped: %s", exc)
            return []
