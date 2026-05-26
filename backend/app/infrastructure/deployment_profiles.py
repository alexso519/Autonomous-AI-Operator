"""
Deployment profiles and environment capability detection.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class DeploymentProfile(str, Enum):
    LOCAL = "local"
    LOCAL_DEV = "local_dev"
    SINGLE_NODE = "single_node"
    DISTRIBUTED_CLUSTER = "distributed_cluster"
    ENTERPRISE = "enterprise"
    CLOUD_FEDERATED = "cloud_federated"


class DeploymentProfiles:
    """Detect and apply deployment profiles."""

    @staticmethod
    def detect_profile() -> DeploymentProfile:
        explicit = os.getenv("DEPLOYMENT_PROFILE", "").lower()
        if explicit in DeploymentProfile._value2member_map_:
            return DeploymentProfile(explicit)

        if settings.enable_runtime_mesh and settings.enable_autoscaling:
            return DeploymentProfile.CLOUD_FEDERATED
        if settings.enable_rbac and settings.enable_multi_tenancy:
            return DeploymentProfile.ENTERPRISE
        if settings.enable_distributed_runtime and settings.redis_url:
            return DeploymentProfile.DISTRIBUTED_CLUSTER
        if settings.enable_distributed_runtime:
            return DeploymentProfile.SINGLE_NODE
        return DeploymentProfile.LOCAL_DEV

    @staticmethod
    def get_capabilities() -> dict[str, Any]:
        profile = DeploymentProfiles.detect_profile()
        redis_available = False
        postgres_available = False

        if settings.redis_url:
            try:
                import redis  # noqa: F401

                redis_available = True
            except ImportError:
                pass

        if settings.postgres_url:
            try:
                import asyncpg  # noqa: F401

                postgres_available = True
            except ImportError:
                pass

        return {
            "profile": profile.value,
            "distributedRuntime": settings.enable_distributed_runtime,
            "runtimeMesh": settings.enable_runtime_mesh,
            "policyEngine": settings.enable_policy_engine,
            "costMetering": settings.enable_cost_metering,
            "infraIntelligence": settings.enable_infra_intelligence,
            "autoscaling": settings.enable_autoscaling,
            "approvalWorkflows": settings.enable_approval_workflows,
            "complianceMode": settings.compliance_mode,
            "rbac": settings.enable_rbac,
            "multiTenancy": settings.enable_multi_tenancy,
            "otelTracing": settings.enable_otel_tracing,
            "redisAvailable": redis_available,
            "postgresAvailable": postgres_available,
            "queueBackend": "redis" if redis_available and settings.redis_url else "sqlite",
        }

    @staticmethod
    def profile_config(profile: DeploymentProfile) -> dict[str, Any]:
        configs = {
            DeploymentProfile.LOCAL_DEV: {
                "enableDistributedRuntime": False,
                "enableRbac": False,
                "maxWorkerConcurrency": 2,
            },
            DeploymentProfile.SINGLE_NODE: {
                "enableDistributedRuntime": True,
                "enableRbac": False,
                "maxWorkerConcurrency": 4,
            },
            DeploymentProfile.DISTRIBUTED_CLUSTER: {
                "enableDistributedRuntime": True,
                "enableRbac": False,
                "maxWorkerConcurrency": 8,
            },
            DeploymentProfile.ENTERPRISE: {
                "enableDistributedRuntime": True,
                "enableRbac": True,
                "enableMultiTenancy": True,
                "maxWorkerConcurrency": 16,
            },
            DeploymentProfile.CLOUD_FEDERATED: {
                "enableDistributedRuntime": True,
                "enableRuntimeMesh": True,
                "enableAutoscaling": True,
                "enableRbac": True,
                "enableMultiTenancy": True,
                "maxWorkerConcurrency": 32,
            },
        }
        return configs.get(profile, configs[DeploymentProfile.LOCAL_DEV])
