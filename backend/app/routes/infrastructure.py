"""
Infrastructure control plane API — workers, queue, observability, auth.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator
from app.infrastructure.distributed_queue import DistributedQueue
from app.infrastructure.worker_runtime import WorkerRuntime
from app.infrastructure.execution_recovery import ExecutionRecovery
from app.infrastructure.metrics_aggregator import MetricsAggregator
from app.infrastructure.runtime_observability import RuntimeObservability
from app.infrastructure.distributed_tracing import DistributedTracing
from app.infrastructure.tenant_runtime import TenantRuntime
from app.infrastructure.runtime_scaler import RuntimeScaler
from app.infrastructure.degraded_mode_manager import DegradedModeManager
from app.infrastructure.deployment_profiles import DeploymentProfiles
from app.infrastructure.runtime_config_validator import RuntimeConfigValidator
from app.infrastructure.production_guardrails import ProductionGuardrails
from app.infrastructure.api_gateway import APIGateway
from app.infrastructure.rbac import Permission

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/infrastructure", tags=["infrastructure"])


class RegisterWorkerRequest(BaseModel):
    hostname: str = "local"
    capabilities: list[str] | None = None
    maxConcurrency: int | None = None


@router.get("/dashboard")
async def infrastructure_dashboard(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    return await InfrastructureCoordinator.get_instance().get_dashboard()


@router.get("/workers")
async def list_workers(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.MANAGE_WORKERS)
    workers = await WorkerRuntime.get_instance().list_workers(include_unhealthy=True)
    return {"workers": [w.to_dict() for w in workers]}


@router.post("/workers/register")
async def register_worker(
    body: RegisterWorkerRequest, request: Request
) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.MANAGE_WORKERS)
    worker = await WorkerRuntime.get_instance().register(
        hostname=body.hostname,
        capabilities=body.capabilities,
        max_concurrency=body.maxConcurrency,
    )
    return {"worker": worker.to_dict()}


@router.get("/queue")
async def queue_status(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    depth = await DistributedQueue.get_instance().queue_depth()
    from app.infrastructure.infrastructure_events import emit_global_infrastructure_event

    pending = depth.get("pending", 0)
    if pending > 50:
        await emit_global_infrastructure_event(
            "queue_pressure_detected",
            f"Queue pressure: {pending} pending jobs",
            queueDepth=pending,
        )
    return {"queue": depth}


@router.get("/recovery/{execution_id}")
async def recovery_timeline(execution_id: str, request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_EXECUTION)
    timeline = await ExecutionRecovery.get_recovery_timeline(execution_id)
    from app.infrastructure.state_snapshot_manager import StateSnapshotManager

    lineage = await StateSnapshotManager.get_lineage(execution_id)
    return {"timeline": timeline, "lineage": lineage}


@router.post("/recovery/{execution_id}/restore")
async def restore_execution(execution_id: str, request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.EXECUTE_WORKFLOW)
    result = await ExecutionRecovery.restore_execution(execution_id)
    if not result:
        raise HTTPException(status_code=404, detail="Execution not found")
    return result


@router.get("/metrics")
async def prometheus_metrics() -> PlainTextResponse:
    return PlainTextResponse(
        MetricsAggregator.prometheus_text(),
        media_type="text/plain; version=0.0.4",
    )


@router.get("/health")
async def runtime_health() -> dict[str, Any]:
    summary = await RuntimeObservability.collect_health_summary()
    return summary.to_dict()


@router.get("/traces")
async def recent_traces(request: Request, limit: int = 50) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    return {"traces": DistributedTracing.get_recent_spans(limit=limit)}


@router.get("/tenants/{tenant_id}")
async def tenant_status(tenant_id: str, request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.MANAGE_TENANTS)
    config = await TenantRuntime.get_config(tenant_id)
    quota = await TenantRuntime.check_quota(tenant_id)
    return {"config": config.to_dict(), "quota": quota}


@router.get("/scaling")
async def scaling_status(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    return await RuntimeScaler.evaluate_scaling()


@router.get("/degraded")
async def degraded_status() -> dict[str, Any]:
    return DegradedModeManager.status()


@router.get("/profile")
async def deployment_profile() -> dict[str, Any]:
    return {
        "profile": DeploymentProfiles.detect_profile().value,
        "capabilities": DeploymentProfiles.get_capabilities(),
        "validation": RuntimeConfigValidator.validate().to_dict(),
        "guardrails": ProductionGuardrails.full_check(),
    }


@router.get("/mesh")
async def runtime_mesh_topology(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    from app.infrastructure.runtime_mesh import RuntimeMesh

    return await RuntimeMesh.get_instance().get_topology()


@router.get("/governance")
async def governance_audit(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    from app.infrastructure.governance_manager import GovernanceManager

    return await GovernanceManager.get_instance().get_audit_summary()


@router.get("/metering")
async def runtime_metering_dashboard(
    request: Request, tenant_id: str = "default"
) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    from app.infrastructure.runtime_metering import RuntimeMetering

    return await RuntimeMetering.get_instance().get_dashboard(tenant_id=tenant_id)


@router.get("/intelligence")
async def infrastructure_intelligence(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    from app.infrastructure.infrastructure_intelligence import InfrastructureIntelligence

    return await InfrastructureIntelligence.get_instance().get_dashboard()


@router.get("/federation")
async def federation_status(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.VIEW_METRICS)
    from app.infrastructure.runtime_federation import RuntimeFederation

    return await RuntimeFederation.get_instance().get_federation_status()


@router.post("/approvals/{request_id}/grant")
async def grant_approval(request_id: str, request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.EXECUTE_WORKFLOW)
    from app.infrastructure.approval_workflows import ApprovalWorkflows

    result = await ApprovalWorkflows.decide(request_id, granted=True, decided_by="api")
    if not result:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return result


@router.post("/approvals/{request_id}/deny")
async def deny_approval(request_id: str, request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.EXECUTE_WORKFLOW)
    from app.infrastructure.approval_workflows import ApprovalWorkflows

    result = await ApprovalWorkflows.decide(request_id, granted=False, decided_by="api")
    if not result:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return result


@router.get("/release")
async def release_info() -> dict[str, Any]:
    from app.infrastructure.release_manager import ReleaseManager

    return await ReleaseManager.get_release_info()


@router.post("/dr/snapshot")
async def disaster_recovery_snapshot(request: Request) -> dict[str, Any]:
    await APIGateway.require_permission(request, Permission.MANAGE_WORKERS)
    from app.infrastructure.disaster_recovery import DisasterRecovery

    return await DisasterRecovery.create_snapshot()
