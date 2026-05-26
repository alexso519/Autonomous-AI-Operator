"""
Role-based access control for enterprise runtime operations.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from app.infrastructure.auth_manager import AuthContext

logger = logging.getLogger(__name__)


class Role(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AUTOMATION_AGENT = "automation_agent"


class Permission(str, Enum):
    EXECUTE_WORKFLOW = "execute_workflow"
    VIEW_EXECUTION = "view_execution"
    CANCEL_EXECUTION = "cancel_execution"
    MANAGE_WORKERS = "manage_workers"
    VIEW_METRICS = "view_metrics"
    MANAGE_TENANTS = "manage_tenants"
    RUN_BENCHMARK = "run_benchmark"
    MANAGE_INTELLIGENCE = "manage_intelligence"
    COMPUTER_USE = "computer_use"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),
    Role.OPERATOR: {
        Permission.EXECUTE_WORKFLOW,
        Permission.VIEW_EXECUTION,
        Permission.CANCEL_EXECUTION,
        Permission.VIEW_METRICS,
        Permission.RUN_BENCHMARK,
        Permission.COMPUTER_USE,
    },
    Role.VIEWER: {
        Permission.VIEW_EXECUTION,
        Permission.VIEW_METRICS,
    },
    Role.AUTOMATION_AGENT: {
        Permission.EXECUTE_WORKFLOW,
        Permission.VIEW_EXECUTION,
        Permission.COMPUTER_USE,
    },
}


class RBAC:
    """Permission checks for authenticated contexts."""

    @staticmethod
    def has_permission(ctx: AuthContext, permission: Permission | str) -> bool:
        perm = permission if isinstance(permission, Permission) else Permission(permission)
        user_roles = {Role(r) for r in ctx.roles if r in Role._value2member_map_}
        if Role.ADMIN in user_roles:
            return True
        for role in user_roles:
            if perm in ROLE_PERMISSIONS.get(role, set()):
                return True
        return False

    @staticmethod
    def require_permission(ctx: AuthContext, permission: Permission | str) -> None:
        if not RBAC.has_permission(ctx, permission):
            raise PermissionError(
                f"Role(s) {ctx.roles} lack permission: {permission}"
            )

    @staticmethod
    def filter_by_role(ctx: AuthContext, resource: str) -> dict[str, Any]:
        return {
            "allowed": RBAC.has_permission(ctx, resource),
            "roles": ctx.roles,
            "tenantId": ctx.tenant_id,
        }
