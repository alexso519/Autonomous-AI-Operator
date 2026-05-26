"""
Runtime configuration validation for production deployments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import settings
from app.infrastructure.deployment_profiles import DeploymentProfiles

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    severity: str  # error, warning, info
    field: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"severity": self.severity, "field": self.field, "message": self.message}


@dataclass
class ValidationReport:
    valid: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [i.to_dict() for i in self.issues],
            "profile": DeploymentProfiles.detect_profile().value,
        }


class RuntimeConfigValidator:
    """Validates runtime configuration before production deployment."""

    @staticmethod
    def validate() -> ValidationReport:
        report = ValidationReport()
        profile = DeploymentProfiles.detect_profile()

        if profile.value in ("distributed_cluster", "enterprise"):
            if not settings.redis_url:
                report.issues.append(
                    ValidationIssue(
                        "warning",
                        "REDIS_URL",
                        "Distributed profile without Redis — using SQLite queue fallback",
                    )
                )

        if settings.enable_rbac and not settings.jwt_secret:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "JWT_SECRET",
                    "RBAC enabled without JWT_SECRET — JWT auth unavailable",
                )
            )

        if settings.enable_multi_tenancy and not settings.enable_rbac:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "ENABLE_RBAC",
                    "Multi-tenancy requires RBAC to be enabled",
                )
            )

        if settings.max_worker_concurrency < 1:
            report.issues.append(
                ValidationIssue(
                    "error",
                    "MAX_WORKER_CONCURRENCY",
                    "Must be at least 1",
                )
            )

        if settings.worker_heartbeat_seconds < 5:
            report.issues.append(
                ValidationIssue(
                    "warning",
                    "WORKER_HEARTBEAT_SECONDS",
                    "Very low heartbeat interval may cause false unhealthy detection",
                )
            )

        report.valid = not any(i.severity == "error" for i in report.issues)
        return report
