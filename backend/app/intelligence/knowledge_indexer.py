"""
Knowledge indexer — embed and index execution artifacts for long-term recall.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from app.config.settings import settings
from app.intelligence.vector_memory import VectorMemory

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class KnowledgeIndexer:
    """Index execution outputs, tool results, and research knowledge."""

    @classmethod
    async def index_execution_output(
        cls,
        execution_id: str,
        agent_name: str,
        output: str,
        *,
        node_id: str = "",
        metadata: dict[str, Any] | None = None,
        emit_fn: EmitFn | None = None,
    ) -> str | None:
        if not settings.enable_vector_memory or not output.strip():
            return None

        content = f"[{agent_name}] {output[:3000]}"
        mem_id = await VectorMemory.store(
            content,
            "execution",
            execution_id=execution_id,
            source_id=node_id,
            metadata={"agentName": agent_name, **(metadata or {})},
        )
        if mem_id and emit_fn:
            await emit_fn(
                execution_id,
                "memory_embedded",
                "intelligence",
                f"Indexed execution output from {agent_name}",
                memoryId=mem_id,
                memoryType="execution",
            )
        return mem_id

    @classmethod
    async def index_tool_result(
        cls,
        execution_id: str,
        tool_name: str,
        output: dict[str, Any] | str,
        *,
        emit_fn: EmitFn | None = None,
    ) -> str | None:
        if not settings.enable_vector_memory:
            return None

        text = json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else str(output)
        content = f"Tool {tool_name}: {text[:2000]}"
        mem_id = await VectorMemory.store(
            content,
            "tool_result",
            execution_id=execution_id,
            source_id=tool_name,
            metadata={"toolName": tool_name},
        )
        if mem_id and emit_fn:
            await emit_fn(
                execution_id,
                "memory_embedded",
                "intelligence",
                f"Indexed tool result: {tool_name}",
                memoryId=mem_id,
                memoryType="tool_result",
            )
        return mem_id

    @classmethod
    async def index_research_knowledge(
        cls,
        execution_id: str,
        title: str,
        content: str,
        *,
        source_url: str = "",
        emit_fn: EmitFn | None = None,
    ) -> str | None:
        if not settings.enable_vector_memory or not content.strip():
            return None

        full = f"{title}\n{content[:3000]}"
        mem_id = await VectorMemory.store(
            full,
            "research",
            execution_id=execution_id,
            source_id=source_url,
            metadata={"title": title, "sourceUrl": source_url, "tags": ["research"]},
        )
        if mem_id and emit_fn:
            await emit_fn(
                execution_id,
                "memory_embedded",
                "intelligence",
                f"Indexed research: {title[:60]}",
                memoryId=mem_id,
                memoryType="research",
            )
        return mem_id

    @classmethod
    async def index_from_workflow_memory(
        cls,
        execution_id: str,
        workflow_memory: dict[str, Any],
        *,
        emit_fn: EmitFn | None = None,
    ) -> list[str]:
        """Bulk-index artifacts from compacted workflow memory."""
        if not settings.enable_vector_memory:
            return []

        indexed: list[str] = []

        tool_calls = workflow_memory.get("tool_calls") or []
        if isinstance(tool_calls, list):
            for call in tool_calls[-20:]:
                if call.get("status") != "completed":
                    continue
                mid = await cls.index_tool_result(
                    execution_id,
                    call.get("tool_name", "unknown"),
                    call.get("output") or {},
                    emit_fn=emit_fn,
                )
                if mid:
                    indexed.append(mid)

        research_block = workflow_memory.get("research_context_block") or ""
        if research_block.strip():
            mid = await cls.index_research_knowledge(
                execution_id,
                "Research synthesis",
                research_block,
                emit_fn=emit_fn,
            )
            if mid:
                indexed.append(mid)

        findings = workflow_memory.get("key_findings") or workflow_memory.get("findings")
        if findings:
            text = json.dumps(findings, ensure_ascii=False) if not isinstance(findings, str) else findings
            mid = await VectorMemory.store(
                f"Key findings: {text[:2000]}",
                "research",
                execution_id=execution_id,
                metadata={"tags": ["findings"]},
            )
            if mid:
                indexed.append(mid)

        return indexed
