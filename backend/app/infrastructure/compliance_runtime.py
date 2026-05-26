"""
Compliance profiles and execution classification.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.infrastructure_events import emit_infrastructure_event

logger = logging.getLogger(__name__)

COMPLIANCE_PROFILES = frozenset({"local_dev", "enterprise", "regulated"})


class ComplianceRuntime:
    """Compliance checks for execution governance."""

    @staticmethod
    def active_mode() -> str:
        mode = (settings.compliance_mode or "local_dev").lower()
        return mode if mode in COMPLIANCE_PROFILES else "local_dev"

    @staticmethod
    def enabled() -> bool:
        return settings.enable_policy_engine or settings.compliance_mode != "local_dev"

    @staticmethod
    def classify_execution(
        workflow_metadata: dict[str, Any] | None = None,
    ) -> str:
        meta = workflow_metadata or {}
        if meta.get("pii") or meta.get("regulated"):
            return "regulated"
        if meta.get("financial") or meta.get("payment"):
            return "financial"
        if meta.get("external"):
            return "external"
        return "internal"

    @staticmethod
    async def run_check(
        execution_id: str,
        *,
        classification: str = "internal",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mode = ComplianceRuntime.active_mode()
        checks: dict[str, Any] = {"mode": mode, "classification": classification}
        passed = True

        if mode == "regulated":
            checks["auditRequired"] = True
            checks["encryptionRequired"] = True
            if classification == "regulated" and not (metadata or {}).get("approved"):
                passed = False
                checks["failure"] = "regulated_execution_requires_approval"

        if mode == "enterprise":
            checks["rbacRequired"] = settings.enable_rbac
            checks["tenantIsolation"] = settings.enable_multi_tenancy
            if settings.enable_multi_tenancy and not (metadata or {}).get("tenantId"):
                passed = False
                checks["failure"] = "tenant_id_required"

        try:
            rid = f"comp_{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            db = await get_db()
            await db.execute(
                """INSERT INTO compliance_records
                   (id, execution_id, profile, classification, check_result, passed, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    rid,
                    execution_id,
                    mode,
                    classification,
                    json.dumps(checks),
                    1 if passed else 0,
                    now,
                ),
            )
            await db.commit()
            await emit_infrastructure_event(
                execution_id,
                "compliance_check_completed",
                f"Compliance check {'passed' if passed else 'failed'}",
                profile=mode,
                passed=passed,
                recordId=rid,
            )
        except Exception as exc:
            logger.debug("Compliance record skipped: %s", exc)

        return {"passed": passed, "profile": mode, "checks": checks}
