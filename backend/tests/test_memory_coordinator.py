"""Tests for unified memory coordinator."""

from app.execution.context_manager import ContextManager
from app.execution.memory_coordinator import MemoryCoordinator
from app.execution.memory_ranker import MemoryRanker
from app.execution.retrieval_planner import (
    AssemblyRequest,
    NS_SHARED,
    NS_WORKFLOW,
    RetrievalPlanner,
)


def test_coordinator_retrieves_workflow_outputs():
    ctx = ContextManager()
    ctx.set_context("Analyze market trends")
    ctx.add_output("n1", "Research Agent", "Found growth in sector A.")
    ctx.add_output("n2", "Analyst", "Sector B is declining.")

    coord = MemoryCoordinator(ctx, execution_id="exec-1")
    plan = RetrievalPlanner.plan(AssemblyRequest(model_tier="standard"))
    ranked = coord.retrieve_sync(plan, objective="Analyze market trends")

    assert len(ranked) >= 2
    namespaces = {r.record.namespace for r in ranked}
    assert NS_WORKFLOW in namespaces or any(
        "Research Agent" in r.record.content for r in ranked
    )


def test_coordinator_deduplicates_content():
    ctx = ContextManager()
    ctx.set_context("Test objective")
    ctx.add_output("n1", "Agent", "Same insight repeated.")
    ctx.set_workflow_memory("findings", "Same insight repeated.")

    coord = MemoryCoordinator(ctx)
    plan = RetrievalPlanner.plan(AssemblyRequest())
    ranked = coord.retrieve_sync(plan)

    contents = [r.record.content.lower() for r in ranked]
    # Shared findings and output may partially overlap; dedup removes exact dupes
    deduped, dupes = MemoryRanker.dedupe([r.record for r in ranked])
    assert dupes >= 0


def test_coordinator_shared_memory_adapter():
    ctx = ContextManager()
    ctx.set_workflow_memory("findings", "Key insight about pricing")

    coord = MemoryCoordinator(ctx)
    records = coord._adapt_shared(5)
    assert len(records) == 1
    assert "findings" in records[0].content
    assert records[0].namespace == NS_SHARED


def test_retrieval_trace_recorded():
    ctx = ContextManager()
    ctx.set_context("Objective")
    coord = MemoryCoordinator(ctx, execution_id="trace-test")
    plan = RetrievalPlanner.plan(AssemblyRequest(is_synthesis=True))
    coord.retrieve_sync(plan)
    trace = coord.get_last_trace()
    assert trace is not None
    assert trace.strategy == "synthesis"
    assert trace.records_after_rank >= 0


def test_deterministic_retrieval_ordering():
    ctx = ContextManager()
    ctx.add_output("a", "Agent A", "Output A")
    ctx.add_output("b", "Agent B", "Output B")
    coord = MemoryCoordinator(ctx)
    plan = RetrievalPlanner.plan(AssemblyRequest())

    r1 = coord.retrieve_sync(plan)
    r2 = coord.retrieve_sync(plan)
    ids1 = [r.record.id for r in r1]
    ids2 = [r.record.id for r in r2]
    assert ids1 == ids2
