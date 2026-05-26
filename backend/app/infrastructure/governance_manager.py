"""
Execution governance — orchestrates policy, compliance, and approvals.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class GovernanceManager:
    """Central governance facade for execution lifecycle hooks."""

    _instance: GovernanceManager | None = None

    @classmethod
    def get_instance(cls) -> GovernanceManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def enabled() -> bool:
        return (
            settings.enable_policy_engine
            or settings.enable_approval_workflows
            or settings.compliance_mode not in ("", "local_dev")
        )

    async def on_execution_start(
        self,
        execution_id: str,
        *,
        tenant_id: str = "default",
        workflow_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"governed": False}
        result: dict[str, Any] = {"governed": True}
        try:
            from app.infrastructure.compliance_runtime import ComplianceRuntime

            classification = ComplianceRuntime.classify_execution(workflow_metadata)
            compliance = await ComplianceRuntime.run_check(
                execution_id,
                classification=classification,
                metadata={"tenantId": tenant_id, **(workflow_metadata or {})},
            )
            result["compliance"] = compliance
            if not compliance.get("passed", True):
                result["blocked"] = True
        except Exception as exc:
            logger.debug("Governance start hook skipped: %s", exc)
            result["degraded"] = True
        return result

    async def evaluate_action(
        self,
        execution_id: str,
        action: str,
        *,
        capability: str = "",
        tool_name: str = "",
        tenant_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled():
            return {"allowed": True}
        try:
            from app.infrastructure.policy_engine import PolicyEngine
            from app.infrastructure.approval_workflows import ApprovalWorkflows

            policy = await PolicyEngine.evaluate(
                execution_id,
                action,
                capability=capability,
                tool_name=tool_name,
                tenant_id=tenant_id,
                metadata=metadata,
            )
            if not policy.get("allowed", True):
                return {"allowed": False, "reason": "policy_violation", "policy": policy}

            if ApprovalWorkflows.requires_approval(capability, action):
                approval = await ApprovalWorkflows.request_approval(
                    execution_id, action, capability=capability
                )
                if approval.get("required") and not approval.get("approved"):
                    return {"allowed": False, "reason": "approval_required", "approval": approval}

            return {"allowed": True, "policy": policy}
        except Exception as exc:
            logger.warning("Governance evaluate degraded (allow): %s", exc)
            return {"allowed": True, "degraded": True}

    async def get_audit_summary(self, *, limit: int = 50) -> dict[str, Any]:
        try:
            from app.infrastructure.policy_engine import PolicyEngine
            from app.infrastructure.approval_workflows import ApprovalWorkflows

            return {
                "violations": await PolicyEngine.list_violations(limit=limit),
                "pendingApprovals": await ApprovalWorkflows.list_pending(limit=limit),
                "complianceMode": settings.compliance_mode,
            }
        except Exception as exc:
            logger.debug("Audit summary skipped: %s", exc)
            return {"violations": [], "pendingApprovals": []}
