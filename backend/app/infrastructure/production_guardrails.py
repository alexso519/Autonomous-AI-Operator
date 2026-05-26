"""
Production guardrails — dangerous config detection and execution safety.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

_DANGEROUS_PATTERNS = [
    (r"(?i)password\s*=\s*\S+", "Hardcoded password detected in config"),
    (r"(?i)secret\s*=\s*['\"]?(test|demo|changeme)", "Weak secret value detected"),
    (r"(?i)debug\s*=\s*true", "Debug mode enabled in production profile"),
]


@dataclass
class GuardrailResult:
    allowed: bool
    violations: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": self.violations,
            "warnings": self.warnings,
        }


class ProductionGuardrails:
    """Enforces production safety constraints."""

    @staticmethod
    def validate_secrets() -> GuardrailResult:
        violations: list[str] = []
        warnings: list[str] = []

        if settings.enable_rbac:
            if not settings.jwt_secret:
                warnings.append("JWT_SECRET not set — JWT authentication disabled")
            elif len(settings.jwt_secret) < 32:
                violations.append("JWT_SECRET must be at least 32 characters")

        return GuardrailResult(
            allowed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    @staticmethod
    def validate_queue_backend() -> GuardrailResult:
        warnings: list[str] = []
        if settings.enable_distributed_runtime and not settings.redis_url:
            warnings.append(
                "Distributed runtime enabled without Redis — SQLite queue fallback active"
            )
        return GuardrailResult(allowed=True, violations=[], warnings=warnings)

    @staticmethod
    def check_config_text(config_text: str) -> GuardrailResult:
        violations: list[str] = []
        for pattern, message in _DANGEROUS_PATTERNS:
            if re.search(pattern, config_text):
                violations.append(message)
        return GuardrailResult(
            allowed=len(violations) == 0,
            violations=violations,
            warnings=[],
        )

    @staticmethod
    def enforce_execution_safety(*, tenant_id: str = "default") -> GuardrailResult:
        from app.infrastructure.tenant_runtime import TenantRuntime

        violations: list[str] = []
        warnings: list[str] = []

        if settings.enable_multi_tenancy:
            quota = TenantRuntime._active_counts.get(tenant_id, 0)
            if quota >= 50:
                violations.append(f"Tenant {tenant_id} exceeded safety execution limit")

        return GuardrailResult(
            allowed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )

    @staticmethod
    def full_check() -> dict[str, Any]:
        return {
            "secrets": ProductionGuardrails.validate_secrets().to_dict(),
            "queue": ProductionGuardrails.validate_queue_backend().to_dict(),
        }
