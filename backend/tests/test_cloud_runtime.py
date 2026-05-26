"""
Tests for cloud-native runtime infrastructure.
"""

import asyncio
import os
import sys

sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..")))


def test_cloud_runtime_context():
    from app.infrastructure.cloud_runtime import CloudRuntime

    ctx = CloudRuntime.get_runtime_context()
    assert "podName" in ctx
    assert "deploymentProfile" in ctx


def test_autoscaling_disabled():
    os.environ["ENABLE_AUTOSCALING"] = "0"

    async def run():
        from app.infrastructure.autoscaling_controller import AutoscalingController

        result = await AutoscalingController.evaluate()
        assert result.get("scaling") == "disabled"

    asyncio.run(run())


def test_deployment_orchestrator_local():
    os.environ["ENABLE_AUTOSCALING"] = "0"
    os.environ["ENABLE_DISTRIBUTED_RUNTIME"] = "0"

    async def run():
        from app.infrastructure.deployment_orchestrator import DeploymentOrchestrator

        rollout = await DeploymentOrchestrator.start_rollout(version="test")
        assert rollout.get("rollout") is False

    asyncio.run(run())


def test_federation_local_mode():
    os.environ["ENABLE_RUNTIME_MESH"] = "0"

    async def run():
        from app.infrastructure.runtime_federation import RuntimeFederation

        status = await RuntimeFederation.get_instance().get_federation_status()
        assert status.get("federated") is False

    asyncio.run(run())
