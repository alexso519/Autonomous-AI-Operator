"""Tests for central context assembler."""

from app.execution.context_assembler import ContextAssembler, SECTION_ORDER
from app.execution.context_manager import ContextManager
from app.execution.retrieval_planner import AssemblyRequest


def test_assemble_sync_includes_workflow_context():
    ctx = ContextManager()
    ctx.set_context("Start here")
    ctx.set_workflow_memory("findings", "Key insight")
    ctx.add_output("node_2", "Writer Agent", "Drafted a plan.")

    assembler = ContextAssembler.for_context(ctx)
    result = assembler.assemble_sync(AssemblyRequest(model_tier="standard"))

    assert "Workflow Context:\nStart here" in result.context
    assert "- findings: Key insight" in result.context
    assert "[Writer Agent]" in result.context
    assert result.estimated_tokens > 0


def test_synthesis_assembly_is_deterministic():
    ctx = ContextManager()
    ctx.set_context("Synthesize findings")
    ctx.add_output("n1", "Researcher", "Finding one about markets.")
    ctx.set_workflow_memory("autonomous_research_mode", True)

    assembler = ContextAssembler.for_context(ctx)
    r1 = assembler.assemble_sync(AssemblyRequest(is_synthesis=True))
    r2 = assembler.assemble_sync(AssemblyRequest(is_synthesis=True))

    assert r1.context == r2.context
    assert r1.replay_token == r2.replay_token


def test_section_order_is_stable():
    ctx = ContextManager()
    ctx.set_context("Objective")
    ctx.set_workflow_memory("findings", "Finding")
    ctx.add_output("n1", "Agent", "Output")

    assembler = ContextAssembler.for_context(ctx)
    result = assembler.assemble_sync(AssemblyRequest(research_mode=True))

    section_names = [s["name"] for s in result.sections]
    indices = [SECTION_ORDER.index(n) if n in SECTION_ORDER else 99 for n in section_names]
    assert indices == sorted(indices)


def test_retry_assembly_smaller_budget():
    ctx = ContextManager()
    ctx.set_context("Retry task")
    ctx.add_output("n1", "Agent", "X" * 3000)

    assembler = ContextAssembler.for_context(ctx)
    normal = assembler.assemble_sync(AssemblyRequest(model_tier="standard"))
    retry = assembler.assemble_sync(AssemblyRequest(is_retry=True))

    assert len(retry.context) <= len(normal.context) + 500


def test_metadata_includes_sources():
    ctx = ContextManager()
    ctx.set_context("Test")
    ctx.add_output("n1", "Agent", "Result")

    assembler = ContextAssembler.for_context(ctx)
    result = assembler.assemble_sync(AssemblyRequest())
    assert "sources" in result.metadata or result.sources
