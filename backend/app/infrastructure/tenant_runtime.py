"""
Multi-tenant runtime isolation and quota enforcement.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class TenantConfig:
    tenant_id: str
    name: str = "Default"
    max_concurrent_executions: int = 10
    max_queue_depth: int = 100
    namespace: str = "default"
    quotas: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "name": self.name,
            "maxConcurrentExecutions": self.max_concurrent_executions,
            "maxQueueDepth": self.max_queue_depth,
            "namespace": self.namespace,
            "quotas": self.quotas,
        }


class TenantRuntime:
    """Tenant-scoped execution isolation."""

    _active_counts: dict[str, int] = {}

    @staticmethod
    async def get_config(tenant_id: str) -> TenantConfig:
        if not settings.enable_multi_tenancy:
            return TenantConfig(tenant_id=tenant_id or "default")

        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM tenant_configs WHERE id = ?", (tenant_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return TenantConfig(tenant_id=tenant_id)
        return TenantConfig(
            tenant_id=row["id"],
            name=row["name"],
            max_concurrent_executions=row["max_concurrent_executions"],
            max_queue_depth=row["max_queue_depth"],
            namespace=row["namespace"] or tenant_id,
            quotas=json.loads(row["quotas"] or "{}"),
        )

    @staticmethod
    async def check_quota(tenant_id: str) -> dict[str, Any]:
        config = await TenantRuntime.get_config(tenant_id)
        active = TenantRuntime._active_counts.get(tenant_id, 0)
        allowed = active < config.max_concurrent_executions
        return {
            "allowed": allowed,
            "activeExecutions": active,
            "maxConcurrent": config.max_concurrent_executions,
            "tenantId": tenant_id,
        }

    @staticmethod
    def acquire_execution_slot(tenant_id: str) -> bool:
        config_sync = TenantRuntime._active_counts.get(tenant_id, 0)
        TenantRuntime._active_counts[tenant_id] = config_sync + 1
        return True

    @staticmethod
    def release_execution_slot(tenant_id: str) -> None:
        current = TenantRuntime._active_counts.get(tenant_id, 0)
        TenantRuntime._active_counts[tenant_id] = max(0, current - 1)

    @staticmethod
    async def record_usage(
        tenant_id: str,
        *,
        execution_id: str,
        action: str,
        units: float = 1.0,
    ) -> None:
        if not settings.enable_multi_tenancy:
            return
        db = await get_db()
        await db.execute(
            """INSERT INTO tenant_usage
               (id, tenant_id, execution_id, action, units, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:16],
                tenant_id,
                execution_id,
                action,
                units,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()

    @staticmethod
    async def ensure_default_tenant() -> None:
        if not settings.enable_multi_tenancy:
            return
        db = await get_db()
        cursor = await db.execute("SELECT id FROM tenant_configs WHERE id = 'default'")
        if await cursor.fetchone():
            return
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO tenant_configs
               (id, name, max_concurrent_executions, max_queue_depth, namespace, quotas, created_at)
               VALUES ('default', 'Default Tenant', 10, 100, 'default', '{}', ?)""",
            (now,),
        )
        await db.commit()

    @staticmethod
    def namespace_for(tenant_id: str) -> str:
        return f"tenant:{tenant_id}"
