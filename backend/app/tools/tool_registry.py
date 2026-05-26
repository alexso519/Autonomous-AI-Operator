from typing import Any

from app.tools.tool_safety import permission_level_for
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
    WebSearchInput,
    WebSearchOutput,
    WebpageFetchInput,
    WebpageFetchOutput,
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


def _register_safe(
    name: str,
    description: str,
    input_model: type,
    output_model: type,
) -> None:
    tool_registry.register(
        ToolDefinition(
            name=name,
            description=description,
            category="safe",
            permission_level=permission_level_for(name),  # type: ignore[arg-type]
            input_model=input_model,
            output_model=output_model,
            requires_approval=False,
        )
    )


def _register_restricted(
    name: str,
    description: str,
    input_model: type,
    output_model: type,
) -> None:
    tool_registry.register(
        ToolDefinition(
            name=name,
            description=description,
            category="approval_required",
            permission_level="approval_required",
            input_model=input_model,
            output_model=output_model,
            requires_approval=True,
        )
    )


# Safe tools — auto-approve
_register_safe(
    "calculator",
    "Deterministic arithmetic evaluation for safe numeric calculations.",
    CalculatorInput,
    CalculatorOutput,
)
_register_safe(
    "json_transform",
    "Deterministic JSON restructuring and extraction operations.",
    JsonTransformInput,
    JsonTransformOutput,
)
_register_safe(
    "markdown_generator",
    "Generate structured markdown from titles, sections, and raw content.",
    MarkdownGeneratorInput,
    MarkdownGeneratorOutput,
)
_register_safe(
    "structured_data_extractor",
    "Extract structured values from raw text using deterministic regex patterns.",
    StructuredDataExtractorInput,
    StructuredDataExtractorOutput,
)
_register_safe(
    "file_reader",
    "Read-only sandboxed file access for safe content inspection.",
    FileReaderInput,
    FileReaderOutput,
)
_register_safe(
    "web_search",
    "Search the web for factual information. Use for research instead of guessing.",
    WebSearchInput,
    WebSearchOutput,
)
_register_safe(
    "webpage_fetch",
    "Fetch and extract text from a public web page (GET only, sandboxed).",
    WebpageFetchInput,
    WebpageFetchOutput,
)

# Restricted tools — require human approval
_register_restricted(
    "shell_command",
    "Execute an allowlisted shell command in a sandboxed environment.",
    ShellCommandInput,
    ShellCommandOutput,
)
_register_restricted(
    "file_write",
    "Write file content to an approved sandboxed path.",
    FileWriteInput,
    FileWriteOutput,
)
_register_restricted(
    "file_delete",
    "Delete a sandboxed file path after explicit approval.",
    FileDeleteInput,
    FileDeleteOutput,
)
_register_restricted(
    "http_post",
    "Send an HTTP POST request after explicit approval.",
    HttpPostInput,
    HttpPostOutput,
)
