"""
Authentication manager — API key and JWT validation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from app.config.settings import settings
from app.database.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class AuthContext:
    user_id: str
    tenant_id: str = "default"
    roles: list[str] = field(default_factory=lambda: ["viewer"])
    auth_method: str = "anonymous"

    def to_dict(self) -> dict[str, Any]:
        return {
            "userId": self.user_id,
            "tenantId": self.tenant_id,
            "roles": self.roles,
            "authMethod": self.auth_method,
        }


class AuthManager:
    """API key and JWT authentication."""

    _default_admin: AuthContext | None = None

    @classmethod
    async def authenticate(
        cls,
        *,
        api_key: str | None = None,
        bearer_token: str | None = None,
    ) -> AuthContext | None:
        if not settings.enable_rbac:
            return AuthContext(
                user_id="local",
                tenant_id="default",
                roles=["admin", "operator", "viewer", "automation_agent"],
                auth_method="local_dev",
            )

        if api_key:
            return await cls._authenticate_api_key(api_key)
        if bearer_token:
            return cls._authenticate_jwt(bearer_token)
        return None

    @classmethod
    async def _authenticate_api_key(cls, api_key: str) -> AuthContext | None:
        key_hash = cls._hash_key(api_key)
        db = await get_db()
        cursor = await db.execute(
            """SELECT k.*, u.roles, u.tenant_id
               FROM api_keys k
               JOIN users u ON u.id = k.user_id
               WHERE k.key_hash = ? AND k.revoked_at IS NULL""",
            (key_hash,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        await db.commit()
        return AuthContext(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"] or "default",
            roles=json.loads(row["roles"] or '["viewer"]'),
            auth_method="api_key",
        )

    @classmethod
    def _authenticate_jwt(cls, token: str) -> AuthContext | None:
        if not settings.jwt_secret:
            return None
        try:
            import base64

            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            exp = payload.get("exp")
            if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(
                timezone.utc
            ):
                return None
            sig_input = f"{parts[0]}.{parts[1]}"
            expected = hmac.new(
                settings.jwt_secret.encode(),
                sig_input.encode(),
                hashlib.sha256,
            ).digest()
            actual = base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4))
            if not hmac.compare_digest(expected, actual):
                return None
            return AuthContext(
                user_id=payload.get("sub", "unknown"),
                tenant_id=payload.get("tenant_id", "default"),
                roles=payload.get("roles", ["viewer"]),
                auth_method="jwt",
            )
        except Exception as exc:
            logger.debug("JWT validation failed: %s", exc)
            return None

    @classmethod
    async def create_api_key(
        cls,
        user_id: str,
        *,
        label: str = "",
        expires_days: int = 365,
    ) -> tuple[str, str]:
        raw_key = f"aai_{secrets.token_urlsafe(32)}"
        key_id = uuid.uuid4().hex[:16]
        key_hash = cls._hash_key(raw_key)
        expires = (
            datetime.now(timezone.utc) + timedelta(days=expires_days)
        ).isoformat()
        db = await get_db()
        await db.execute(
            """INSERT INTO api_keys
               (id, user_id, key_hash, label, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                key_id,
                user_id,
                key_hash,
                label,
                expires,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
        return key_id, raw_key

    @classmethod
    async def ensure_default_admin(cls) -> None:
        if not settings.enable_rbac:
            return
        db = await get_db()
        cursor = await db.execute("SELECT id FROM users WHERE id = 'admin'")
        if await cursor.fetchone():
            return
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """INSERT INTO users (id, email, roles, tenant_id, created_at)
               VALUES ('admin', 'admin@local', '["admin","operator"]', 'default', ?)""",
            (now,),
        )
        await db.commit()

    @staticmethod
    def _hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()
