"""
Safe terminal orchestration — isolated shell execution with allowlists and provenance.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.tools.permissions import SHELL_WHITELIST

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

# Extended allowlist for coding/terminal operations (still safe)
TERMINAL_ALLOWLIST = SHELL_WHITELIST | {
    "python", "pytest", "pip", "npm", "node", "git",
    "dir", "find", "grep", "wc", "head", "tail",
}

DESTRUCTIVE_PATTERNS = (
    "rm -rf", "del /f", "format", "mkfs", "dd if=",
    "> /dev/", "shutdown", "reboot", ":(){", "chmod 777",
)


@dataclass
class TerminalCommand:
    command: str
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    timeout_seconds: int = 60

    def full_command(self) -> str:
        parts = [self.command] + self.args
        return " ".join(shlex.quote(p) for p in parts)


@dataclass
class TerminalResult:
    command_id: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    success: bool
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "commandId": self.command_id,
            "command": self.command,
            "stdout": self.stdout[:8000],
            "stderr": self.stderr[:2000],
            "exitCode": self.exit_code,
            "durationMs": self.duration_ms,
            "success": self.success,
            "startedAt": self.started_at,
        }


@dataclass
class CommandChainResult:
    chain_id: str
    results: list[TerminalResult]
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "chainId": self.chain_id,
            "results": [r.to_dict() for r in self.results],
            "success": self.success,
        }


class TerminalRuntime:
    """Isolated terminal execution with safety controls and event streaming."""

    def __init__(
        self,
        workspace: Path,
        *,
        max_history: int = 50,
        default_timeout: int = 60,
    ) -> None:
        self.workspace = workspace.resolve()
        self.max_history = max_history
        self.default_timeout = default_timeout
        self.history: list[TerminalResult] = []

    @classmethod
    def validate_command(cls, command: str, args: list[str] | None = None) -> None:
        """Validate command against allowlist and block destructive patterns."""
        args = args or []
        full = f"{command} {' '.join(args)}".lower()

        for pattern in DESTRUCTIVE_PATTERNS:
            if pattern in full:
                raise ValueError(f"Destructive command blocked: {pattern}")

        try:
            parsed = shlex.split(command)
        except ValueError as exc:
            raise ValueError(f"Invalid command syntax: {exc}") from exc

        if not parsed:
            raise ValueError("Empty command")

        executable = parsed[0].lower()
        if executable not in TERMINAL_ALLOWLIST:
            raise ValueError(
                f"Command '{executable}' not in terminal allowlist"
            )

        for arg in args:
            if any(ch in arg for ch in [";", "&&", "||", "|", "$", "`"]):
                raise ValueError("Shell injection characters blocked in arguments")

    async def execute(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
        timeout: int | None = None,
        agent: str = "TerminalOperator",
    ) -> TerminalResult:
        """Execute a single command with streaming output events."""
        args = args or []
        self.validate_command(command, args)
        command_id = f"cmd-{uuid.uuid4().hex[:8]}"
        timeout = timeout or self.default_timeout

        try:
            parsed = shlex.split(command)
        except ValueError:
            parsed = [command]
        executable = parsed[0] if parsed else command
        all_args = parsed[1:] + list(args)
        full_cmd = " ".join(shlex.quote(p) for p in [executable] + all_args)

        if emit_fn and execution_id:
            await emit_fn(
                execution_id,
                "terminal_started",
                agent,
                f"$ {full_cmd}",
                commandId=command_id,
                command=full_cmd,
            )

        started = datetime.now(timezone.utc)
        try:
            proc = await asyncio.create_subprocess_shell(
                full_cmd,
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = proc.returncode or 0
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
        except asyncio.TimeoutError:
            exit_code = -1
            stdout = ""
            stderr = f"Command timed out after {timeout}s"
        except Exception as exc:
            exit_code = -1
            stdout = ""
            stderr = str(exc)

        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        result = TerminalResult(
            command_id=command_id,
            command=full_cmd,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            success=exit_code == 0,
        )

        self._record(result)

        if emit_fn and execution_id:
            if stdout.strip():
                await emit_fn(
                    execution_id,
                    "terminal_output",
                    agent,
                    stdout[:2000],
                    commandId=command_id,
                    stream="stdout",
                )
            await emit_fn(
                execution_id,
                "terminal_completed",
                agent,
                f"Exit {exit_code} ({duration_ms}ms)",
                **result.to_dict(),
            )

        return result

    async def execute_chain(
        self,
        commands: list[tuple[str, list[str]]],
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
        stop_on_failure: bool = True,
    ) -> CommandChainResult:
        """Execute a retryable chain of commands."""
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"
        results: list[TerminalResult] = []
        success = True

        for cmd, args in commands:
            result = await self.execute(
                cmd, args,
                execution_id=execution_id,
                emit_fn=emit_fn,
            )
            results.append(result)
            if not result.success:
                success = False
                if stop_on_failure:
                    break

        return CommandChainResult(chain_id=chain_id, results=results, success=success)

    def get_history(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.history]

    def _record(self, result: TerminalResult) -> None:
        self.history.append(result)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
