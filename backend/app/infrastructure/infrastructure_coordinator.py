"""
Infrastructure coordinator — startup, hooks, and cross-module orchestration.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class InfrastructureCoordinator:
    """Central coordinator for production infrastructure subsystems."""

    _instance: InfrastructureCoordinator | None = None
    _maintenance_task: asyncio.Task | None = None

    def __init__(self) -> None:
        self._initialized = False

    @classmethod
    def get_instance(cls) -> InfrastructureCoordinator:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    async def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return {"status": "already_initialized"}

        from app.infrastructure.deployment_profiles import DeploymentProfiles
        from app.infrastructure.runtime_config_validator import RuntimeConfigValidator
        from app.infrastructure.production_guardrails import ProductionGuardrails
        from app.infrastructure.worker_runtime import WorkerRuntime
        from app.infrastructure.execution_recovery import ExecutionRecovery
        from app.infrastructure.auth_manager import AuthManager
        from app.infrastructure.tenant_runtime import TenantRuntime

        profile = DeploymentProfiles.detect_profile()
        validation = RuntimeConfigValidator.validate()
        guardrails = ProductionGuardrails.full_check()

        worker = await WorkerRuntime.get_instance().ensure_local_worker()
        recovery = await ExecutionRecovery.recover_on_startup()

        if settings.enable_rbac:
            await AuthManager.ensure_default_admin()
        if settings.enable_multi_tenancy:
            await TenantRuntime.ensure_default_tenant()

        if settings.enable_distributed_runtime:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

        if settings.enable_runtime_mesh:
            try:
                from app.infrastructure.runtime_mesh import RuntimeMesh

                await RuntimeMesh.get_instance().initialize()
            except Exception as exc:
                logger.debug("Runtime mesh init skipped: %s", exc)

        if settings.enable_policy_engine:
            try:
                from app.infrastructure.policy_engine import PolicyEngine

                await PolicyEngine.ensure_default_policies()
            except Exception as exc:
                logger.debug("Policy seed skipped: %s", exc)

        try:
            from app.infrastructure.runtime_migrations import RuntimeMigrations

            await RuntimeMigrations.apply_pending()
        except Exception as exc:
            logger.debug("Runtime migrations skipped: %s", exc)

        self._initialized = True
        logger.info(
            "Infrastructure initialized: profile=%s worker=%s",
            profile.value,
            worker.worker_id,
        )
        return {
            "profile": profile.value,
            "validation": validation.to_dict(),
            "guardrails": guardrails,
            "workerId": worker.worker_id,
            "recovery": recovery,
        }

    async def shutdown(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        self._initialized = False

    async def on_execution_start(
        self,
        execution_id: str,
        workflow_id: str,
        *,
        tenant_id: str = "default",
    ) -> None:
        from app.infrastructure.execution_persistence import ExecutionPersistence
        from app.infrastructure.metrics_aggregator import MetricsAggregator
        from app.infrastructure.tenant_runtime import TenantRuntime
        from app.infrastructure.distributed_tracing import DistributedTracing

        if settings.enable_multi_tenancy:
            TenantRuntime.acquire_execution_slot(tenant_id)

        await ExecutionPersistence.create(execution_id, workflow_id, tenant_id=tenant_id)
        MetricsAggregator.increment("executions.started")
        DistributedTracing.start_span(
            "execution",
            execution_id=execution_id,
            workflowId=workflow_id,
        )

        if settings.enable_distributed_runtime:
            from app.infrastructure.job_dispatcher import JobDispatcher

            await JobDispatcher.get_instance().dispatch_execution(
                execution_id, workflow_id, tenant_id=tenant_id
            )

        if settings.enable_runtime_mesh:
            try:
                from app.infrastructure.runtime_mesh import RuntimeMesh

                await RuntimeMesh.get_instance().on_execution_start(
                    execution_id, workflow_id, tenant_id=tenant_id
                )
            except Exception as exc:
                logger.debug("Mesh start hook skipped: %s", exc)

        if settings.enable_policy_engine or settings.enable_approval_workflows:
            try:
                from app.infrastructure.governance_manager import GovernanceManager

                gov = await GovernanceManager.get_instance().on_execution_start(
                    execution_id, tenant_id=tenant_id
                )
                if gov.get("blocked"):
                    logger.warning("Governance blocked execution %s", execution_id)
            except Exception as exc:
                logger.debug("Governance start hook skipped: %s", exc)

        if settings.enable_cost_metering:
            try:
                from app.infrastructure.budget_enforcer import BudgetEnforcer

                budget = await BudgetEnforcer.check_and_charge(
                    execution_id, 0.0, tenant_id=tenant_id
                )
                if not budget.get("allowed", True):
                    logger.warning("Budget blocked execution %s", execution_id)
            except Exception as exc:
                logger.debug("Budget start hook skipped: %s", exc)

    async def on_execution_checkpoint(self, execution_id: str) -> None:
        from app.infrastructure.state_snapshot_manager import StateSnapshotManager
        from app.infrastructure.execution_persistence import ExecutionPersistence

        snapshot = await StateSnapshotManager.capture_from_kernel(execution_id)
        await ExecutionPersistence.update_checkpoint(
            execution_id,
            snapshot.to_dict(),
        )

    async def on_execution_complete(
        self,
        execution_id: str,
        *,
        tenant_id: str = "default",
        status: str = "completed",
    ) -> None:
        from app.infrastructure.execution_persistence import ExecutionPersistence
        from app.infrastructure.metrics_aggregator import MetricsAggregator
        from app.infrastructure.tenant_runtime import TenantRuntime

        if status == "completed":
            await ExecutionPersistence.mark_completed(execution_id)
        MetricsAggregator.increment(f"executions.{status}")
        if settings.enable_multi_tenancy:
            TenantRuntime.release_execution_slot(tenant_id)

        if settings.enable_runtime_mesh:
            try:
                from app.infrastructure.runtime_mesh import RuntimeMesh

                await RuntimeMesh.get_instance().on_execution_complete(
                    execution_id, status=status
                )
            except Exception as exc:
                logger.debug("Mesh complete hook skipped: %s", exc)

        if settings.enable_cost_metering:
            try:
                from app.infrastructure.runtime_metering import RuntimeMetering

                await RuntimeMetering.get_instance().on_execution_complete(
                    execution_id, tenant_id=tenant_id
                )
            except Exception as exc:
                logger.debug("Metering complete hook skipped: %s", exc)

    async def get_dashboard(self) -> dict[str, Any]:
        from app.infrastructure.job_dispatcher import JobDispatcher
        from app.infrastructure.runtime_observability import RuntimeObservability
        from app.infrastructure.degraded_mode_manager import DegradedModeManager
        from app.infrastructure.runtime_scaler import RuntimeScaler
        from app.infrastructure.deployment_profiles import DeploymentProfiles

        dispatch = await JobDispatcher.get_instance().get_dispatch_stats()
        health = await RuntimeObservability.collect_health_summary()
        scaling = await RuntimeScaler.evaluate_scaling()

        dashboard: dict[str, Any] = {
            "profile": DeploymentProfiles.detect_profile().value,
            "dispatch": dispatch,
            "health": health.to_dict(),
            "degraded": DegradedModeManager.status(),
            "scaling": scaling,
        }

        if settings.enable_runtime_mesh:
            try:
                from app.infrastructure.runtime_mesh import RuntimeMesh

                dashboard["mesh"] = await RuntimeMesh.get_instance().get_topology()
            except Exception as exc:
                logger.debug("Mesh dashboard skipped: %s", exc)

        if settings.enable_policy_engine:
            try:
                from app.infrastructure.governance_manager import GovernanceManager

                dashboard["governance"] = await GovernanceManager.get_instance().get_audit_summary()
            except Exception as exc:
                logger.debug("Governance dashboard skipped: %s", exc)

        if settings.enable_cost_metering:
            try:
                from app.infrastructure.runtime_metering import RuntimeMetering

                dashboard["metering"] = await RuntimeMetering.get_instance().get_dashboard()
            except Exception as exc:
                logger.debug("Metering dashboard skipped: %s", exc)

        if settings.enable_infra_intelligence:
            try:
                from app.infrastructure.infrastructure_intelligence import (
                    InfrastructureIntelligence,
                )

                dashboard["intelligence"] = (
                    await InfrastructureIntelligence.get_instance().get_dashboard()
                )
            except Exception as exc:
                logger.debug("Intelligence dashboard skipped: %s", exc)

        return dashboard

    async def _maintenance_loop(self) -> None:
        from app.infrastructure.failover_manager import FailoverManager
        from app.infrastructure.job_dispatcher import JobDispatcher
        from app.infrastructure.worker_runtime import WorkerRuntime
        from app.infrastructure.degraded_mode_manager import DegradedModeManager
        from app.infrastructure.distributed_queue import DistributedQueue

        while True:
            try:
                await asyncio.sleep(settings.worker_heartbeat_seconds)
                worker_id = WorkerRuntime.get_instance().get_local_worker_id()
                if worker_id:
                    await WorkerRuntime.get_instance().heartbeat(worker_id)
                await JobDispatcher.get_instance().renew_active_leases()
                await FailoverManager.run_failover_cycle()
                depth = await DistributedQueue.get_instance().queue_depth()
                from app.execution.manager import execution_manager

                await DegradedModeManager.evaluate_pressure(
                    depth.get("pending", 0),
                    execution_manager.count_active(),
                )
            except asyncio.CancelledError:
                break
                if settings.enable_runtime_mesh:
                    from app.infrastructure.runtime_mesh import RuntimeMesh

                    await RuntimeMesh.get_instance().maintenance_cycle()

                if settings.enable_infra_intelligence:
                    from app.infrastructure.infrastructure_intelligence import (
                        InfrastructureIntelligence,
                    )

                    await InfrastructureIntelligence.get_instance().maintenance_cycle()

                if settings.enable_autoscaling:
                    from app.infrastructure.autoscaling_controller import (
                        AutoscalingController,
                    )

                    await AutoscalingController.evaluate()
            except Exception as exc:
                logger.warning("Infrastructure maintenance cycle error: %s", exc)
