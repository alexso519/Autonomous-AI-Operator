"""
Runtime policy enforcement — security, financial, tool blocking, simulation mode.
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

DEFAULT_POLICIES: list[dict[str, Any]] = [
    {
        "name": "security_baseline",
        "category": "security",
        "rules": {"blockDestructiveTools": True, "requireApprovalForShell": True},
    },
    {
        "name": "financial_guard",
        "category": "financial",
        "rules": {"maxEstimatedCost": 50.0, "requireBudgetCheck": True},
    },
    {
        "name": "browser_restrictions",
        "category": "browser",
        "rules": {"blockExternalNavigation": False, "maxActionsPerMinute": 30},
    },
    {
        "name": "coding_sandbox",
        "category": "coding",
        "rules": {"blockSystemPaths": True, "maxPatchSizeKb": 512},
    },
    {
        "name": "external_api",
        "category": "external_api",
        "rules": {"allowedDomains": [], "blockUnknownHosts": False},
    },
    {
        "name": "tenant_isolation",
        "category": "multi_tenant",
        "rules": {"enforceNamespace": True, "crossTenantBlocked": True},
    },
]


class PolicyEngine:
    """Evaluates and enforces runtime policies."""

    _defaults_seeded = False

    @staticmethod
    def enabled() -> bool:
        return settings.enable_policy_engine

    @staticmethod
    async def ensure_default_policies() -> None:
        if not PolicyEngine.enabled() or PolicyEngine._defaults_seeded:
            return
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            for p in DEFAULT_POLICIES:
                pid = f"pol_{p['name']}"
                cursor = await db.execute(
                    "SELECT id FROM runtime_policies WHERE id = ?", (pid,)
                )
                if await cursor.fetchone():
                    continue
                await db.execute(
                    """INSERT INTO runtime_policies
                       (id, name, category, rules, enabled, simulation_mode,
                        tenant_id, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 1, 0, 'default', ?, ?)""",
                    (pid, p["name"], p["category"], json.dumps(p["rules"]), now, now),
                )
            await db.commit()
            PolicyEngine._defaults_seeded = True
        except Exception as exc:
            logger.warning("Policy seed degraded: %s", exc)

    @staticmethod
    async def list_policies(*, tenant_id: str = "default") -> list[dict[str, Any]]:
        if not PolicyEngine.enabled():
            return []
        try:
            await PolicyEngine.ensure_default_policies()
            db = await get_db()
            cursor = await db.execute(
                """SELECT * FROM runtime_policies
                   WHERE tenant_id IN (?, 'default') AND enabled = 1""",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "policyId": r["id"],
                    "name": r["name"],
                    "category": r["category"],
                    "rules": json.loads(r["rules"] or "{}"),
                    "simulationMode": bool(r["simulation_mode"]),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.debug("List policies skipped: %s", exc)
            return []

    @staticmethod
    async def evaluate(
        execution_id: str,
        action: str,
        *,
        capability: str = "",
        tool_name: str = "",
        tenant_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not PolicyEngine.enabled():
            return {"allowed": True, "violations": []}
        violations: list[dict[str, Any]] = []
        try:
            policies = await PolicyEngine.list_policies(tenant_id=tenant_id)
            for pol in policies:
                rules = pol.get("rules") or {}
                cat = pol.get("category", "")
                blocked = False
                msg = ""

                if cat == "security" and tool_name:
                    destructive = {"rm", "delete", "format", "shutdown"}
                    if rules.get("blockDestructiveTools") and any(
                        d in tool_name.lower() for d in destructive
                    ):
                        blocked = True
                        msg = f"Destructive tool blocked: {tool_name}"

                if cat == "browser" and capability == "browser":
                    max_a = rules.get("maxActionsPerMinute", 999)
                    recent = (metadata or {}).get("browserActionsLastMinute", 0)
                    if recent > max_a:
                        blocked = True
                        msg = "Browser action rate exceeded"

                if cat == "coding" and capability == "coding":
                    path = (metadata or {}).get("targetPath", "")
                    if rules.get("blockSystemPaths") and path.startswith(("/etc", "C:\\Windows")):
                        blocked = True
                        msg = "System path modification blocked"

                if cat == "external_api" and capability == "api":
                    host = (metadata or {}).get("host", "")
                    allowed = rules.get("allowedDomains") or []
                    if rules.get("blockUnknownHosts") and allowed and host not in allowed:
                        blocked = True
                        msg = f"External API host not allowed: {host}"

                if cat == "multi_tenant" and rules.get("crossTenantBlocked"):
                    req_tenant = (metadata or {}).get("requestedTenant", tenant_id)
                    if req_tenant != tenant_id:
                        blocked = True
                        msg = "Cross-tenant access blocked"

                if blocked:
                    vid = f"viol_{uuid.uuid4().hex[:12]}"
                    now = datetime.now(timezone.utc).isoformat()
                    db = await get_db()
                    sim = pol.get("simulationMode", False)
                    await db.execute(
                        """INSERT INTO policy_violations
                           (id, execution_id, policy_id, category, severity, message, metadata, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            vid,
                            execution_id,
                            pol["policyId"],
                            cat,
                            "high" if not sim else "simulated",
                            msg,
                            json.dumps(metadata or {}),
                            now,
                        ),
                    )
                    await db.commit()
                    violations.append(
                        {"violationId": vid, "policyId": pol["policyId"], "message": msg, "simulated": sim}
                    )
                    await emit_infrastructure_event(
                        execution_id,
                        "policy_violation_detected",
                        msg,
                        policyId=pol["policyId"],
                        category=cat,
                        simulated=sim,
                    )

            allowed = not any(not v.get("simulated") for v in violations)
            return {"allowed": allowed or all(v.get("simulated") for v in violations), "violations": violations}
        except Exception as exc:
            logger.warning("Policy evaluation degraded (allow): %s", exc)
            return {"allowed": True, "violations": [], "degraded": True}

    @staticmethod
    async def list_violations(*, execution_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        try:
            db = await get_db()
            if execution_id:
                cursor = await db.execute(
                    """SELECT * FROM policy_violations WHERE execution_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (execution_id, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM policy_violations ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            rows = await cursor.fetchall()
            return [
                {
                    "violationId": r["id"],
                    "executionId": r["execution_id"],
                    "policyId": r["policy_id"],
                    "category": r["category"],
                    "severity": r["severity"],
                    "message": r["message"],
                    "createdAt": r["created_at"],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.debug("List violations skipped: %s", exc)
            return []
