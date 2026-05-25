import re
from typing import Any

from app.tools.tool_models import StructuredDataExtractorInput, ToolExecutionError


def extract_structured_data(input_model: StructuredDataExtractorInput) -> dict[str, Any]:
    text = input_model.text
    data: dict[str, str] = {}

    for field in input_model.schema:
        try:
            regex = re.compile(field.pattern, re.IGNORECASE | re.MULTILINE)
        except re.error as exc:
            raise ToolExecutionError(
                f"Invalid regex for field '{field.name}': {exc}"
            ) from exc

        match = regex.search(text)
        if not match:
            data[field.name] = ""
            continue

        if match.lastindex:
            data[field.name] = match.group(1).strip()
        else:
            data[field.name] = match.group(0).strip()

    return {"data": data}
