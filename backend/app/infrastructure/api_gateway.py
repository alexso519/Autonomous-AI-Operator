"""
API gateway middleware — auth, throttling, audit logging.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from app.config.settings import settings
from app.database.database import get_db
from app.infrastructure.auth_manager import AuthContext, AuthManager
from app.infrastructure.rbac import Permission, RBAC

logger = logging.getLogger(__name__)

_REQUEST_COUNTS: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 120  # requests per minute per tenant


class APIGateway:
    """Request authentication, throttling, and audit."""

    @staticmethod
    async def authenticate_request(request: Request) -> AuthContext:
        api_key = request.headers.get("X-API-Key")
        auth_header = request.headers.get("Authorization", "")
        bearer = auth_header[7:] if auth_header.startswith("Bearer ") else None

        ctx = await AuthManager.authenticate(api_key=api_key, bearer_token=bearer)
        if ctx is None and settings.enable_rbac:
            raise HTTPException(status_code=401, detail="Authentication required")
        if ctx is None:
            ctx = AuthContext(user_id="anonymous", auth_method="anonymous")
        request.state.auth = ctx
        return ctx

    @staticmethod
    async def check_rate_limit(ctx: AuthContext) -> None:
        if not settings.enable_rbac:
            return
        now = time.time()
        key = ctx.tenant_id
        window = [t for t in _REQUEST_COUNTS[key] if now - t < 60]
        if len(window) >= _RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        window.append(now)
        _REQUEST_COUNTS[key] = window

    @staticmethod
    async def require_permission(
        request: Request, permission: Permission | str
    ) -> AuthContext:
        ctx = getattr(request.state, "auth", None)
        if ctx is None:
            ctx = await APIGateway.authenticate_request(request)
        await APIGateway.check_rate_limit(ctx)
        if settings.enable_rbac:
            if not RBAC.has_permission(ctx, permission):
                raise HTTPException(status_code=403, detail="Insufficient permissions")
        return ctx

    @staticmethod
    async def audit_log(
        ctx: AuthContext,
        *,
        action: str,
        resource: str,
        metadata: dict[str, Any] | None = None,
        request: Request | None = None,
    ) -> None:
        if not settings.enable_rbac:
            return
        db = await get_db()
        await db.execute(
            """INSERT INTO audit_logs
               (id, user_id, tenant_id, action, resource, metadata, ip_address, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:16],
                ctx.user_id,
                ctx.tenant_id,
                action,
                resource,
                json.dumps(metadata or {}),
                request.client.host if request and request.client else "",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
