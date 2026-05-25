from typing import Any

from app.tools.tool_models import (
    FileDeleteInput,
    FileDeleteOutput,
    FileReaderInput,
    FileReaderOutput,
    FileWriteInput,
    FileWriteOutput,
    HttpPostInput,
    HttpPostOutput,
    JsonTransformInput,
    JsonTransformOutput,
    MarkdownGeneratorInput,
    MarkdownGeneratorOutput,
    ShellCommandInput,
    ShellCommandOutput,
    StructuredDataExtractorInput,
    StructuredDataExtractorOutput,
    ToolDefinition,
    ToolBaseInput,
    CalculatorInput,
    CalculatorOutput,
)


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"Tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, tool_name: str) -> ToolDefinition:
        if tool_name not in self._definitions:
            raise KeyError(f"Unknown tool: {tool_name}")
        return self._definitions[tool_name]

    def list(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def validate_input(self, tool_name: str, payload: dict[str, Any]) -> ToolBaseInput:
        definition = self.get(tool_name)
        return definition.input_model(**payload)


tool_registry = ToolRegistry()

# Safe tools
for definition in [
    ToolDefinition(
        name="calculator",
        description="Deterministic arithmetic evaluation for safe numeric calculations.",
        category="safe",
        permission_level="safe",
        input_model=CalculatorInput,
        output_model=CalculatorOutput,
        requires_approval=False,
    ),
    ToolDefinition(
        name="json_transform",
        description="Deterministic JSON restructuring and extraction operations.",
        category="safe",
        permission_level="safe",
        input_model=JsonTransformInput,
        output_model=JsonTransformOutput,
        requires_approval=False,
    ),
    ToolDefinition(
        name="markdown_generator",
        description="Generate structured markdown from titles, sections, and raw content.",
        category="safe",
        permission_level="safe",
        input_model=MarkdownGeneratorInput,
        output_model=MarkdownGeneratorOutput,
        requires_approval=False,
    ),
    ToolDefinition(
        name="structured_data_extractor",
        description="Extract structured values from raw text using deterministic regex patterns.",
        category="safe",
        permission_level="safe",
        input_model=StructuredDataExtractorInput,
        output_model=StructuredDataExtractorOutput,
        requires_approval=False,
    ),
    ToolDefinition(
        name="file_reader",
        description="Read-only sandboxed file access for safe content inspection.",
        category="safe",
        permission_level="restricted",
        input_model=FileReaderInput,
        output_model=FileReaderOutput,
        requires_approval=False,
    ),
]:
    tool_registry.register(definition)

# Approval-gated tools
for definition in [
    ToolDefinition(
        name="shell_command",
        description="Execute an allowlisted shell command in a sandboxed environment.",
        category="approval_required",
        permission_level="approval_required",
        input_model=ShellCommandInput,
        output_model=ShellCommandOutput,
        requires_approval=True,
    ),
    ToolDefinition(
        name="file_write",
        description="Write file content to an approved sandboxed path.",
        category="approval_required",
        permission_level="approval_required",
        input_model=FileWriteInput,
        output_model=FileWriteOutput,
        requires_approval=True,
    ),
    ToolDefinition(
        name="file_delete",
        description="Delete a sandboxed file path after explicit approval.",
        category="approval_required",
        permission_level="approval_required",
        input_model=FileDeleteInput,
        output_model=FileDeleteOutput,
        requires_approval=True,
    ),
    ToolDefinition(
        name="http_post",
        description="Send an HTTP POST request after explicit approval.",
        category="approval_required",
        permission_level="approval_required",
        input_model=HttpPostInput,
        output_model=HttpPostOutput,
        requires_approval=True,
    ),
]:
    tool_registry.register(definition)
