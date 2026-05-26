"""Tests for production readiness layer."""

import pytest

from app.execution.model_router import ModelRouter, RoutingContext, RoutingTier
from app.execution.agent_spawn_optimizer import AgentSpawnOptimizer
from app.services.task_analyzer import (
    TaskAnalysis,
    TaskType,
    Complexity,
    RiskLevel,
    RequiredAgent,
)
from app.tools.tool_efficiency import ToolEfficiencyLayer
from app.execution.production_safety import ProductionSafetyGuard


def test_model_router_simple_task_uses_lightweight():
    ctx = RoutingContext(
        task_complexity="simple",
        needs_tools=False,
        context_token_estimate=500,
    )
    decision = ModelRouter.route(ctx)
    assert decision.tier == RoutingTier.LIGHTWEIGHT
    assert "simple_task" in " ".join(decision.reasons)


def test_model_router_synthesis_uses_strong_tier():
    ctx = RoutingContext(is_synthesis=True, task_complexity="moderate")
    decision = ModelRouter.route(ctx)
    assert decision.tier == RoutingTier.SYNTHESIS


def test_spawn_optimizer_caps_simple_tasks():
    analysis = TaskAnalysis(
        objective="Say hello",
        task_type=TaskType.CONTENT_CREATION,
        complexity=Complexity.SIMPLE,
        risk_level=RiskLevel.LOW,
        required_agents=[
            RequiredAgent(role="A", goal="g1", sequence_order=0),
            RequiredAgent(role="B", goal="g2", sequence_order=1),
            RequiredAgent(role="C", goal="g3", sequence_order=2),
        ],
    )
    plan = AgentSpawnOptimizer.optimize(analysis)
    assert plan.agent_count <= 2
    assert plan.mode == "lightweight"


def test_tool_cache_key_deterministic():
    k1 = ToolEfficiencyLayer.cache_key("web_search", {"query": "AI chips"})
    k2 = ToolEfficiencyLayer.cache_key("web_search", {"query": "AI chips"})
    k3 = ToolEfficiencyLayer.cache_key("web_search", {"query": "other"})
    assert k1 == k2
    assert k1 != k3


def test_retry_circuit_breaker():
    eid = "test-exec-circuit"
    ProductionSafetyGuard.clear_execution(eid)
    for _ in range(ProductionSafetyGuard.MAX_RETRY_SPIRAL):
        ProductionSafetyGuard.record_retry(eid)
    result = ProductionSafetyGuard.check_retry_circuit(eid)
    assert not result.allowed
    ProductionSafetyGuard.clear_execution(eid)


def test_rate_limit_blocks_when_saturated():
    ProductionSafetyGuard._execution_starts.clear()
    for _ in range(ProductionSafetyGuard.RATE_LIMIT_MAX_STARTS):
        ProductionSafetyGuard._execution_starts.append(__import__("time").time())
    result = ProductionSafetyGuard.check_rate_limit(active_count=0)
    assert not result.allowed
