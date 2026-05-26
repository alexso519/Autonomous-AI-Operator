"""
Test runner — detect and run tests with structured output.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


@dataclass
class TestFailure:
    test_name: str
    message: str
    file_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "testName": self.test_name,
            "message": self.message,
            "filePath": self.file_path,
        }


@dataclass
class TestRunResult:
    run_id: str
    command: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    failures: list[TestFailure] = field(default_factory=list)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "command": self.command,
            "passed": self.passed,
            "exitCode": self.exit_code,
            "stdout": self.stdout[:4000],
            "stderr": self.stderr[:2000],
            "durationMs": self.duration_ms,
            "failures": [f.to_dict() for f in self.failures],
            "startedAt": self.started_at,
        }


class TestRunner:
    """Run tests in a workspace with event streaming."""

    PYTEST_FAIL = re.compile(r"^(FAILED|ERROR)\s+(.+)$", re.M)
    PYTEST_FILE = re.compile(r"^(.*?\.py):\d+:", re.M)

    @classmethod
    async def run(
        cls,
        workspace: Path,
        command: str,
        *,
        execution_id: str = "",
        emit_fn: EmitFn | None = None,
        timeout: int = 120,
    ) -> TestRunResult:
        run_id = f"test-{uuid.uuid4().hex[:8]}"
        started = datetime.now(timezone.utc)

        if emit_fn and execution_id:
            await emit_fn(
                execution_id,
                "test_started",
                "TestEngineer",
                f"Running tests: {command}",
                runId=run_id,
                command=command,
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workspace),
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
            stderr = f"Test command timed out after {timeout}s"
        except Exception as exc:
            exit_code = -1
            stdout = ""
            stderr = str(exc)

        duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        passed = exit_code == 0
        failures = cls._parse_failures(stdout + "\n" + stderr)

        result = TestRunResult(
            run_id=run_id,
            command=command,
            passed=passed,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            failures=failures,
        )

        if emit_fn and execution_id:
            event_type = "test_passed" if passed else "test_failed"
            await emit_fn(
                execution_id,
                event_type,
                "TestEngineer",
                f"Tests {'passed' if passed else 'failed'} (exit {exit_code})",
                **result.to_dict(),
            )

        return result

    @classmethod
    def _parse_failures(cls, output: str) -> list[TestFailure]:
        failures: list[TestFailure] = []
        for match in cls.PYTEST_FAIL.finditer(output):
            test_name = match.group(2).strip()
            file_match = cls.PYTEST_FILE.search(output, match.start())
            file_path = file_match.group(1) if file_match else ""
            failures.append(TestFailure(
                test_name=test_name,
                message=match.group(0).strip(),
                file_path=file_path,
            ))
        if not failures and "FAILED" in output:
            failures.append(TestFailure(
                test_name="unknown",
                message=output[-500:],
            ))
        return failures[:10]

    @classmethod
    def detect_test_command(cls, workspace: Path) -> str:
        if (workspace / "pytest.ini").exists() or list(workspace.rglob("test_*.py")):
            return "python -m pytest -q --tb=short"
        if (workspace / "package.json").exists():
            return "npm test --if-present"
        return "python -m pytest -q --tb=short 2>/dev/null || echo 'no tests found'"
