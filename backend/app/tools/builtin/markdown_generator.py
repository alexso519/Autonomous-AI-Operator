from typing import Any

from app.tools.tool_models import MarkdownGeneratorInput, ToolExecutionError


def generate_markdown(input_model: MarkdownGeneratorInput) -> dict[str, Any]:
    title = input_model.title.strip()
    if not title:
        raise ToolExecutionError("Markdown title cannot be empty.")

    sections = input_model.sections or []
    if not sections and not input_model.content:
        raise ToolExecutionError("Markdown content or sections must be provided.")

    lines: list[str] = [f"# {title}", ""]
    if sections:
        for section in sections:
            heading = section.get("heading", "Section").strip()
            body = section.get("body", "").strip()
            lines.append(f"## {heading}")
            lines.append("")
            lines.append(body)
            lines.append("")
    else:
        lines.append(input_model.content.strip())

    markdown = "\n".join(line for line in lines if line is not None)
    return {"markdown": markdown}
