"""
Secure runtime configuration and secrets handling.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class SecurityHardening:
    """Runtime security hardening checks."""

    @staticmethod
    def validate_configuration() -> dict[str, Any]:
        issues: list[str] = []
        if settings.enable_rbac and not settings.jwt_secret:
            issues.append("JWT_SECRET required when RBAC enabled")
        if settings.compliance_mode == "regulated" and not settings.jwt_secret:
            issues.append("JWT_SECRET required for regulated compliance")
        if settings.enable_distributed_runtime and not settings.redis_url:
            issues.append("REDIS_URL recommended for distributed runtime")
        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "encryptedSecretsSupported": bool(os.getenv("SECRETS_ENCRYPTION_KEY", "")),
        }

    @staticmethod
    def redact_secrets(config: dict[str, Any]) -> dict[str, Any]:
        sensitive = {"jwt_secret", "password", "api_key", "token", "secret"}
        redacted = {}
        for k, v in config.items():
            if any(s in k.lower() for s in sensitive):
                redacted[k] = "***"
            elif isinstance(v, dict):
                redacted[k] = SecurityHardening.redact_secrets(v)
            else:
                redacted[k] = v
        return redacted

    @staticmethod
    async def backup_validation() -> dict[str, Any]:
        try:
            from app.infrastructure.disaster_recovery import DisasterRecovery

            snap = await DisasterRecovery.create_snapshot(label="validation")
            return {
                "valid": snap.get("snapshotId") is not None,
                "recordCount": snap.get("recordCount", 0),
            }
        except Exception as exc:
            logger.warning("Backup validation degraded: %s", exc)
            return {"valid": False, "degraded": True}
