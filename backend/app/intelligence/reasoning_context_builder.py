"""
Reasoning context builder — assembles knowledge-grounded context blocks.
"""

from __future__ import annotations

from typing import Any

from app.intelligence.cognitive_retriever import CognitiveRetriever
from app.intelligence.knowledge_synthesizer import KnowledgeSynthesizer


class ReasoningContextBuilder:
    """Build injectable context from graph, causal, and synthesis layers."""

    @classmethod
    def format_graph_paths(cls, paths: list[dict[str, Any]]) -> str:
        if not paths:
            return ""
        lines = ["[Knowledge graph paths]"]
        for p in paths[:5]:
            hops = p.get("hops") or p.get("steps") or []
            if hops:
                chain = " → ".join(
                    str(h.get("to", h.get("endId", "")))[:30] for h in hops[:4]
                )
                lines.append(f"- ({p.get('score', 0):.2f}) {chain}")
        return "\n".join(lines)

    @classmethod
    def format_synthesis(cls, synthesis: dict[str, Any] | None) -> str:
        if not synthesis:
            return ""
        summary = synthesis.get("summary", "")
        if not summary:
            return ""
        return f"[Synthesized knowledge]\n{summary[:600]}"

    @classmethod
    def format_causal(cls, causal_links: list[dict[str, Any]]) -> str:
        if not causal_links:
            return ""
        lines = ["[Causal links]"]
        for c in causal_links[:4]:
            lines.append(f"- {c.get('causeEntityId', '?')} → {c.get('effectEntityId', '?')} ({c.get('confidence', 0):.2f})")
        return "\n".join(lines)

    @classmethod
    async def build_context_block(
        cls,
        objective: str,
        *,
        execution_id: str = "",
        confidence: float = 0.5,
        uncertainty: float = 0.5,
        include_synthesis: bool = True,
        emit_fn=None,
    ) -> str:
        retrieval = await CognitiveRetriever.retrieve_for_cognition(
            objective,
            execution_id=execution_id,
            confidence=confidence,
            uncertainty=uncertainty,
            emit_fn=emit_fn,
        )

        blocks: list[str] = []
        semantic_block = SemanticRetriever.format_context_block(retrieval.get("memories", []))
        if semantic_block:
            blocks.append(semantic_block)

        graph = retrieval.get("graphContext") or {}
        path_block = cls.format_graph_paths(graph.get("graphPaths", []) + graph.get("crossEntityPaths", []))
        if path_block:
            blocks.append(path_block)

        if include_synthesis:
            syn_result = await KnowledgeSynthesizer.synthesize_for_objective(
                objective, execution_id=execution_id, emit_fn=emit_fn,
            )
            syn_block = cls.format_synthesis(syn_result.get("synthesis"))
            if syn_block:
                blocks.append(syn_block)

        return "\n\n".join(blocks)

    @classmethod
    async def build_snapshot(cls, objective: str, execution_id: str = "") -> dict[str, Any]:
        from app.intelligence.causal_engine import CausalEngine
        from app.intelligence.concept_mapper import ConceptMapper

        retrieval = await CognitiveRetriever.retrieve_for_cognition(
            objective, execution_id=execution_id,
        )
        return {
            "objective": objective,
            "memories": retrieval.get("memories", []),
            "graphContext": retrieval.get("graphContext", {}),
            "causalHistory": await CausalEngine.get_causal_history(limit=10),
            "strategicInsights": await ConceptMapper.get_insights(limit=5),
        }


# Lazy import alias for format_context_block
from app.intelligence.semantic_retriever import SemanticRetriever  # noqa: E402
