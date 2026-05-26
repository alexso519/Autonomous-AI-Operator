"""Tests for unified capability registry."""

import asyncio

from app.runtime.capability_registry import (
    CapabilityDefinition,
    CapabilityHealth,
    CapabilityPolicy,
    CapabilityRegistry,
)


def test_builtin_capabilities_registered():
    registry = CapabilityRegistry("test-exec-1")
    caps = registry.list_all()
    assert len(caps) >= 16
    ids = {c.id for c in caps}
    assert "research" in ids
    assert "coding" in ids
    assert "browser" in ids


def test_lookup_by_task_type():
    registry = CapabilityRegistry("test-exec-2")
    matches = registry.lookup_by_task_type("research")
    assert len(matches) >= 1
    assert matches[0].id in ("research", "web_search", "webpage_fetch")


def test_select_capability():
    async def _run():
        registry = CapabilityRegistry("test-exec-3")
        matches = registry.lookup_by_task_type("coding")
        ids = {m.id for m in matches}
        assert "coding" in ids

    asyncio.run(_run())


def test_dependency_graph():
    registry = CapabilityRegistry("test-exec-4")
    graph = registry.dependency_graph()
    assert "coding" in graph
    assert "terminal" in graph["coding"]


def test_deny_policy_blocks_selection():
    async def _run():
        registry = CapabilityRegistry("test-exec-5")
        denied = CapabilityDefinition(
            id="blocked_cap",
            name="Blocked",
            task_types=["blocked"],
            policy=CapabilityPolicy(allowed=False),
        )
        await registry.register(denied)
        cap = await registry.select("blocked")
        assert cap is None

    asyncio.run(_run())


def test_cost_estimation():
    registry = CapabilityRegistry("test-exec-6")
    cost = registry.estimate_cost(["research", "web_search", "synthesis"])
    assert cost >= 3.0


def test_failure_degrades_health():
    registry = CapabilityRegistry("test-exec-7")
    registry.record_failure("browser")
    registry.record_failure("browser")
    registry.record_failure("browser")
    cap = registry.get("browser")
    assert cap.health == CapabilityHealth.DEGRADED


def test_local_model_suitability():
    registry = CapabilityRegistry("test-exec-8")
    score = registry.local_model_suitability("web_search")
    assert score >= 0.9
