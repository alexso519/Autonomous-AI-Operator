import shlex
import subprocess
from typing import Any

from app.tools.permissions import validate_shell_command
from app.tools.tool_models import ShellCommandInput, ToolExecutionError


def execute_shell_command(input_model: ShellCommandInput) -> dict[str, Any]:
    validate_shell_command(input_model.command, input_model.args)
    try:
        full_command = shlex.split(input_model.command) + list(input_model.args)
    except ValueError as exc:
        raise ToolExecutionError(f"Invalid shell command syntax: {exc}") from exc

    try:
        process = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=input_model.timeout_seconds,
            shell=False,
            check=False,
        )
        stdout = process.stdout.strip()
        stderr = process.stderr.strip()
        exit_code = process.returncode
    except subprocess.TimeoutExpired as exc:
        raise ToolExecutionError(
            f"Shell command timed out after {input_model.timeout_seconds} seconds."
        ) from exc
    except FileNotFoundError as exc:
        raise ToolExecutionError(
            f"Shell command executable not found: {exc}"
        ) from exc
    except Exception as exc:
        raise ToolExecutionError(f"Shell command execution failed: {exc}") from exc

    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
    }
