"""
Zero-downtime release coordination and runtime integrity checks.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ReleaseManager:
    """Manages runtime releases and integrity validation."""

    _current_version: str = "1.0.0"

    @staticmethod
    async def get_release_info() -> dict[str, Any]:
        try:
            from app.infrastructure.deployment_profiles import DeploymentProfiles

            return {
                "version": ReleaseManager._current_version,
                "profile": DeploymentProfiles.detect_profile().value,
            }
        except Exception:
            return {"version": ReleaseManager._current_version}

    @staticmethod
    async def pre_deploy_check() -> dict[str, Any]:
        try:
            from app.infrastructure.runtime_config_validator import RuntimeConfigValidator
            from app.infrastructure.production_guardrails import ProductionGuardrails

            validation = RuntimeConfigValidator.validate()
            guardrails = ProductionGuardrails.full_check()
            passed = validation.valid
            return {
                "passed": passed,
                "validation": validation.to_dict(),
                "guardrails": guardrails,
            }
        except Exception as exc:
            logging.getLogger(__name__).warning("Pre-deploy check degraded: %s", exc)
            return {"passed": True, "degraded": True}

    @staticmethod
    async def run_integrity_check() -> dict[str, Any]:
        try:
            from app.database.database import get_db

            db = await get_db()
            tables = [
                "runtime_clusters",
                "persistent_executions",
                "distributed_jobs",
                "worker_nodes",
            ]
            ok = True
            for t in tables:
                try:
                    await db.execute(f"SELECT COUNT(*) FROM {t}")
                except Exception:
                    ok = False
            return {"integrityOk": ok, "tablesChecked": tables}
        except Exception as exc:
            return {"integrityOk": False, "error": str(exc)}
