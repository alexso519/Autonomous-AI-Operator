from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.database.database import get_db
from app.tools.sandbox import execute_tool
from app.tools.tool_registry import tool_registry
from app.tools.tool_safety import is_auto_approved, requires_manual_approval

if TYPE_CHECKING:
    from app.execution.context_manager import ContextManager

from app.tools.tool_models import (
    ToolCallRequest,
    ToolExecutionError,
    ToolExecutionResponse,
    ToolExecutionStatus,
    ToolDefinition,
    ToolApprovalRequired,
    serialize_tool_input,
    serialize_tool_output,
)

logger = logging.getLogger(__name__)


class ToolExecutionService:
    """Service for validating, executing, and persisting tool actions."""

    async def execute_tool(
        self,
        request: ToolCallRequest,
        ctx: ContextManager | None = None,
    ) -> ToolExecutionResponse:
        definition = self._get_definition(request.tool_name)
        input_obj = self._validate_request(definition, request)

        # Tool efficiency: cache hit / duplicate suppression
        from app.tools.tool_efficiency import ToolEfficiencyLayer

        efficiency = await ToolEfficiencyLayer.check_before_execute(
            definition.name,
            request.input_data,
            execution_id=request.execution_id,
        )
        if not efficiency.allow and efficiency.reused_result and efficiency.cache_key:
            cached = await ToolEfficiencyLayer.lookup_cache(
                definition.name, request.input_data
            )
            if cached.hit and cached.output:
                if ctx is not None:
                    hits = int(ctx.get_workflow_memory("tool_cache_hits") or 0) + 1
                    ctx.set_workflow_memory("tool_cache_hits", hits)
                if request.execution_id:
                    evt = (
                        "reused_result"
                        if "repeated" in efficiency.message
                        else "cache_hit"
                    )
                    await self._emit_efficiency_event(
                        request.execution_id,
                        definition.name,
                        request.agent_name or "tool",
                        evt,
                        efficiency.cache_key,
                    )
                await self._persist_tool_call(
                    request,
                    definition,
                    status="completed",
                    output=cached.output,
                    error=None,
                    approved=True,
                    auto_approved=True,
                )
                self._record_in_context(
                    ctx,
                    request,
                    definition,
                    status="completed",
                    output=cached.output,
                    error=None,
                    auto_approved=True,
                )
                return ToolExecutionResponse(
                    status="completed",
                    tool_name=definition.name,
                    output=cached.output,
                    error=None,
                    approval_payload=None,
                )
        if not efficiency.allow and efficiency.suppressed:
            raise ToolExecutionError(efficiency.message)

        # Deterministic approval policy — safe tools auto-execute with audit trail.
        needs_approval = requires_manual_approval(definition.name)
        auto_approved = is_auto_approved(definition.name) and not request.approved

        if needs_approval and not request.approved:
            approval_payload = {
                "tool_name": definition.name,
                "description": definition.description,
                "input": request.input_data,
                "permission_level": definition.permission_level,
            }
            await self._persist_tool_call(
                request,
                definition,
                status="pending_approval",
                output=None,
                error=None,
                approved=False,
            )
            await self._persist_pending_approval(
                request,
                definition,
                approval_payload,
            )
            self._record_in_context(
                ctx,
                request,
                definition,
                status="pending_approval",
                output=None,
                error=None,
            )
            if ctx is not None:
                ctx.add_approval_decision(
                    node_id=request.node_id or f"tool_{definition.name}",
                    decision="pending",
                    notes=definition.description,
                )
            return ToolExecutionResponse(
                status="pending_approval",
                tool_name=definition.name,
                output=None,
                error=None,
                approval_payload=approval_payload,
            )

        try:
            # Safe tools run immediately; restricted tools only after approval flag.
            effective_approved = request.approved or auto_approved
            raw_output = await execute_tool(
                definition, input_obj, approved=effective_approved
            )
            output_data = definition.output_model(**raw_output).model_dump()
            await ToolEfficiencyLayer.store_cache(
                definition.name,
                request.input_data,
                output_data,
                execution_id=request.execution_id,
            )
            await self._persist_tool_call(
                request,
                definition,
                status="completed",
                output=output_data,
                error=None,
                approved=effective_approved,
                auto_approved=auto_approved,
            )
            self._record_in_context(
                ctx,
                request,
                definition,
                status="completed",
                output=output_data,
                error=None,
                auto_approved=auto_approved,
            )
            return ToolExecutionResponse(
                status="completed",
                tool_name=definition.name,
                output=output_data,
                error=None,
                approval_payload=None,
            )
        except ToolApprovalRequired as approval_exc:
            await self._persist_tool_call(
                request,
                definition,
                status="pending_approval",
                output=None,
                error=approval_exc.reason,
                approved=False,
            )
            self._record_in_context(
                ctx,
                request,
                definition,
                status="pending_approval",
                output=None,
                error=approval_exc.reason,
            )
            return ToolExecutionResponse(
                status="pending_approval",
                tool_name=definition.name,
                output=None,
                error=approval_exc.reason,
                approval_payload=approval_exc.approval_payload,
            )
        except Exception as exc:
            error_message = str(exc)
            from app.execution.production_safety import ProductionSafetyGuard

            isolated = ProductionSafetyGuard.isolate_tool_failure(
                definition.name, error_message
            )
            logger.exception(
                "Tool execution failed for %s (isolated): %s",
                definition.name,
                error_message,
            )
            if ctx is not None:
                failures = ctx.get_workflow_memory("tool_failures") or []
                failures.append(isolated)
                ctx.set_workflow_memory("tool_failures", failures)
            if ctx is not None:
                from app.execution.reflection_service import ReflectionService

                ReflectionService.record_tool_failure(
                    node_id=request.node_id or definition.name,
                    agent_name=request.agent_name or "tool",
                    error=error_message,
                    ctx=ctx,
                )
            await self._persist_tool_call(
                request,
                definition,
                status="failed",
                output=None,
                error=error_message,
                approved=request.approved,
            )
            self._record_in_context(
                ctx,
                request,
                definition,
                status="failed",
                output=None,
                error=error_message,
            )
            raise ToolExecutionError(error_message) from exc

    def _get_definition(self, tool_name: str) -> ToolDefinition:
        try:
            return tool_registry.get(tool_name)
        except KeyError as exc:
            raise ToolExecutionError(str(exc)) from exc

    def _validate_request(
        self,
        definition: ToolDefinition,
        request: ToolCallRequest,
    ) -> Any:
        try:
            return definition.input_model(**request.input_data)
        except Exception as exc:
            raise ToolExecutionError(
                f"Invalid input for tool '{definition.name}': {exc}"
            ) from exc

    async def _persist_tool_call(
        self,
        request: ToolCallRequest,
        definition: ToolDefinition,
        status: ToolExecutionStatus,
        output: dict[str, Any] | None,
        error: str | None,
        approved: bool,
        auto_approved: bool = False,
    ) -> None:
        if not request.execution_id:
            return

        db = await get_db()
        now = datetime.utcnow().isoformat()
        await db.execute(
            """INSERT INTO tool_calls
               (id, execution_id, node_id, tool_name, category, permission_level,
                approval_required, approved, status, input, output, error, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex,
                request.execution_id,
                request.node_id or "unknown",
                definition.name,
                definition.category,
                definition.permission_level,
                int(definition.requires_approval or requires_manual_approval(definition.name)),
                int(approved),
                status,
                serialize_tool_input(request.input_data),
                serialize_tool_output(output or {}),
                error or "",
                now,
            ),
        )
        await db.commit()

    async def _persist_pending_approval(
        self,
        request: ToolCallRequest,
        definition: ToolDefinition,
        approval_payload: dict[str, Any],
    ) -> None:
        if not request.execution_id:
            return

        db = await get_db()
        approval_id = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        await db.execute(
            """INSERT INTO execution_approvals
               (id, execution_id, node_id, agent_name, status, requested_at, notes)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (
                approval_id,
                request.execution_id,
                request.node_id or f"tool_{definition.name}",
                request.agent_name or definition.name,
                now,
                json.dumps(approval_payload, ensure_ascii=False),
            ),
        )
        await db.commit()

    async def _emit_efficiency_event(
        self,
        execution_id: str,
        tool_name: str,
        agent_name: str,
        event_type: str,
        cache_key: str,
    ) -> None:
        try:
            from app.streaming.event_manager import event_manager, StreamEvent
            from app.tools.tool_efficiency import ToolEfficiencyLayer

            event = StreamEvent(
                type=event_type,
                agent=agent_name,
                message=f"Tool {event_type}: {tool_name}",
                data={"toolName": tool_name, "cacheKey": cache_key},
            )
            await event_manager.emit(execution_id, event)
            await ToolEfficiencyLayer.record_cache_event(
                execution_id, tool_name, cache_key, event_type
            )
        except Exception as exc:
            logger.debug("Efficiency event emit failed: %s", exc)

    def _record_in_context(
        self,
        ctx: ContextManager | None,
        request: ToolCallRequest,
        definition: ToolDefinition,
        status: ToolExecutionStatus,
        output: dict[str, Any] | None,
        error: str | None,
        auto_approved: bool = False,
    ) -> None:
        if ctx is None:
            return

        record = {
            "id": uuid.uuid4().hex,
            "tool_name": definition.name,
            "category": definition.category,
            "permission_level": definition.permission_level,
            "approval_required": requires_manual_approval(definition.name),
            "approved": request.approved or auto_approved,
            "auto_approved": auto_approved,
            "status": status,
            "input": request.input_data,
            "output": output or {},
            "error": error or "",
            "node_id": request.node_id,
            "agent_name": request.agent_name,
            "timestamp": datetime.utcnow().isoformat(),
        }
        ctx.set_workflow_memory(
            "tool_calls",
            (ctx.get_workflow_memory("tool_calls") or []) + [record],
        )
        if request.node_id:
            ctx.add_node_memory(request.node_id, "tool_call", record)
        ctx.add_node_memory(
            request.node_id or definition.name,
            "tool_call_status",
            status,
        )
