from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.database.database import get_db
from app.tools.sandbox import execute_tool
from app.tools.tool_registry import tool_registry

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

        if definition.requires_approval and not request.approved:
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
            raw_output = await execute_tool(definition, input_obj, approved=request.approved)
            output_data = definition.output_model(**raw_output).model_dump()
            await self._persist_tool_call(
                request,
                definition,
                status="completed",
                output=output_data,
                error=None,
                approved=request.approved,
            )
            self._record_in_context(
                ctx,
                request,
                definition,
                status="completed",
                output=output_data,
                error=None,
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
            logger.exception(
                "Tool execution failed for %s: %s",
                definition.name,
                error_message,
            )
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
                int(definition.requires_approval),
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

    def _record_in_context(
        self,
        ctx: ContextManager | None,
        request: ToolCallRequest,
        definition: ToolDefinition,
        status: ToolExecutionStatus,
        output: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        if ctx is None:
            return

        record = {
            "id": uuid.uuid4().hex,
            "tool_name": definition.name,
            "category": definition.category,
            "permission_level": definition.permission_level,
            "approval_required": definition.requires_approval,
            "approved": request.approved,
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
