from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

ToolCategory = Literal["safe", "approval_required"]
PermissionLevel = Literal["safe", "restricted", "approval_required"]
ToolExecutionStatus = Literal["completed", "failed", "pending_approval"]


class ToolBaseInput(BaseModel):
    model_config = {"extra": "forbid"}


class ToolBaseOutput(BaseModel):
    model_config = {"extra": "forbid"}


class CalculatorInput(ToolBaseInput):
    expression: str = Field(..., description="Arithmetic expression to evaluate")


class CalculatorOutput(ToolBaseOutput):
    result: float | int | str = Field(..., description="Evaluated calculator result")
    formatted: str = Field(..., description="Human-readable result string")


class JsonTransformOperation(BaseModel):
    op: Literal["copy", "rename", "remove", "extract"]
    source: str = Field(..., description="Dot-path to the source value")
    target: str | None = Field(None, description="Optional destination dot-path")


class JsonTransformInput(ToolBaseInput):
    data: dict[str, Any] = Field(..., description="JSON-compatible input data")
    operations: list[JsonTransformOperation] = Field(
        ..., description="Ordered list of deterministic transform operations"
    )


class JsonTransformOutput(ToolBaseOutput):
    result: dict[str, Any] = Field(..., description="Transformed JSON object")
    transformed: bool = Field(..., description="Whether any operation changed the input")


class MarkdownGeneratorInput(ToolBaseInput):
    title: str = Field(..., description="Title for the generated markdown")
    sections: list[dict[str, str]] = Field(
        default_factory=list,
        description="Optional structured sections with headings and bodies",
    )
    content: str | None = Field(None, description="Fallback raw content if no sections are provided")


class MarkdownGeneratorOutput(ToolBaseOutput):
    markdown: str = Field(..., description="Generated markdown content")


class StructuredDataExtractorField(BaseModel):
    name: str = Field(..., description="Field name for extracted value")
    pattern: str = Field(..., description="Regex pattern to extract the value")


class StructuredDataExtractorInput(ToolBaseInput):
    text: str = Field(..., description="Raw text to extract structured data from")
    schema: list[StructuredDataExtractorField] = Field(
        ..., description="Schema defining the named extraction patterns"
    )


class StructuredDataExtractorOutput(ToolBaseOutput):
    data: dict[str, str] = Field(..., description="Extracted structured values")


class FileReaderInput(ToolBaseInput):
    path: str = Field(..., description="File path to read")
    max_bytes: int = Field(4096, ge=1, description="Maximum bytes to read from the file")


class FileReaderOutput(ToolBaseOutput):
    path: str = Field(..., description="Resolved file path")
    content: str = Field(..., description="File contents, truncated if needed")
    truncated: bool = Field(..., description="Whether the content was truncated")


class ShellCommandInput(ToolBaseInput):
    command: str = Field(..., description="Command to execute")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    timeout_seconds: int = Field(
        8,
        ge=1,
        le=30,
        description="Maximum seconds to allow the command to run",
    )


class ShellCommandOutput(ToolBaseOutput):
    stdout: str = Field(..., description="Standard output from the command")
    stderr: str = Field(..., description="Standard error output")
    exit_code: int = Field(..., description="Process exit code")


class FileWriteInput(ToolBaseInput):
    path: str = Field(..., description="File path to write")
    content: str = Field(..., description="File content to write")
    mode: Literal["w", "a"] = Field(
        "w",
        description="Write mode: overwrite or append",
    )


class FileWriteOutput(ToolBaseOutput):
    path: str = Field(..., description="Resolved file path")
    bytes_written: int = Field(..., description="Number of bytes written")


class FileDeleteInput(ToolBaseInput):
    path: str = Field(..., description="File path to delete")


class FileDeleteOutput(ToolBaseOutput):
    path: str = Field(..., description="Resolved file path")
    deleted: bool = Field(..., description="Whether the file was deleted")


class HttpPostInput(ToolBaseInput):
    url: str = Field(..., description="Target URL for the POST request")
    headers: dict[str, str] = Field(
        default_factory=dict,
        description="Optional HTTP headers",
    )
    json_body: dict[str, Any] | None = Field(None, description="Optional JSON payload")
    text_body: str | None = Field(None, description="Optional raw text payload")
    timeout_seconds: int = Field(
        10,
        ge=1,
        le=30,
        description="Maximum seconds to wait for the POST response",
    )


class HttpPostOutput(ToolBaseOutput):
    status_code: int = Field(..., description="HTTP response status code")
    response_headers: dict[str, str] = Field(..., description="Response headers")
    response_body: str = Field(..., description="Response body text")


@dataclass
class ToolDefinition:
    name: str
    description: str
    category: ToolCategory
    permission_level: PermissionLevel
    input_model: type[ToolBaseInput]
    output_model: type[ToolBaseOutput]
    requires_approval: bool = False


class ToolCallRequest(BaseModel):
    tool_name: str = Field(..., description="Tool identifier")
    input_data: dict[str, Any] = Field(..., description="Raw input payload for the tool")
    execution_id: str | None = Field(None, description="Optional execution id for persistence")
    node_id: str | None = Field(None, description="Optional node id invoking the tool")
    agent_name: str | None = Field(None, description="Optional agent invoking the tool")
    approved: bool = Field(False, description="Whether approval has already been granted")

    model_config = {"extra": "forbid"}


class ToolExecutionResponse(BaseModel):
    status: ToolExecutionStatus = Field(..., description="Execution result status")
    tool_name: str = Field(..., description="Tool identifier")
    output: dict[str, Any] | None = Field(None, description="Normalized tool output")
    error: str | None = Field(None, description="Error message for failed execution")
    approval_payload: dict[str, Any] | None = Field(
        None,
        description="Approval metadata for approval-gated tools",
    )

    model_config = {"extra": "forbid"}


class ToolExecutionError(Exception):
    pass


class ToolApprovalRequired(ToolExecutionError):
    def __init__(self, tool_name: str, reason: str, approval_payload: dict[str, Any]) -> None:
        super().__init__(reason)
        self.tool_name = tool_name
        self.reason = reason
        self.approval_payload = approval_payload


def serialize_tool_output(output: Any) -> str:
    try:
        return json.dumps(output, ensure_ascii=False)
    except TypeError:
        return str(output)


def serialize_tool_input(input_data: dict[str, Any]) -> str:
    try:
        return json.dumps(input_data, ensure_ascii=False)
    except TypeError:
        return str(input_data)
