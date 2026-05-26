"""Tests for memory ranker."""

from app.execution.memory_ranker import MemoryRanker, MemoryRecord


def test_evidence_ranks_higher_than_retry():
    evidence = MemoryRecord(
        id="e1",
        namespace="evidence",
        content="Grounded fact from tool",
        source="tool_call:web_search",
        confidence=0.9,
        is_evidence=True,
    )
    retry = MemoryRecord(
        id="r1",
        namespace="workflow_outputs",
        content="Retry attempt output",
        source="workflow_output:n2",
        confidence=0.5,
        is_retry=True,
    )

    ranked = MemoryRanker.rank([retry, evidence])
    assert ranked[0].record.id == "e1"
    assert ranked[0].score > ranked[1].score


def test_ranking_reasons_are_included():
    record = MemoryRecord(
        id="x1",
        namespace="research",
        content="Research finding",
        source="research_memory",
        confidence=0.8,
        is_evidence=True,
    )
    ranked = MemoryRanker.rank([record])
    assert ranked[0].reasons
    assert any("confidence" in r for r in ranked[0].reasons)


def test_dedupe_removes_identical_content():
    a = MemoryRecord(
        id="a", namespace="shared", content="Same text", source="s1"
    )
    b = MemoryRecord(
        id="b", namespace="workflow_outputs", content="Same text", source="s2"
    )
    unique, dupes = MemoryRanker.dedupe([a, b])
    assert len(unique) == 1
    assert dupes == 1


def test_stable_ordering_on_tie():
    records = [
        MemoryRecord(id="b", namespace="general", content="B", source="s", confidence=0.5),
        MemoryRecord(id="a", namespace="general", content="A", source="s", confidence=0.5),
    ]
    r1 = MemoryRanker.rank(records)
    r2 = MemoryRanker.rank(records)
    assert [r.record.id for r in r1] == [r.record.id for r in r2]


def test_synthesis_boost():
    record = MemoryRecord(
        id="s1",
        namespace="research",
        content="Important finding",
        source="research",
        is_synthesis_relevant=True,
        confidence=0.6,
    )
    normal = MemoryRanker.score_record(record, is_synthesis=False)
    synth = MemoryRanker.score_record(record, is_synthesis=True)
    assert synth.score >= normal.score
