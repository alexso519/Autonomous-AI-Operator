"""Tests for safe terminal runtime."""

import asyncio
import sys
from pathlib import Path
from types import ModuleType

import pytest

crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
sys.modules["crewai"] = crewai

from app.tools.terminal_runtime import TerminalRuntime


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


async def _terminal_echo(workspace):
    runtime = TerminalRuntime(workspace)
    emitted = []

    async def emit_fn(eid, event_type, agent, message, **data):
        emitted.append(event_type)

    result = await runtime.execute(
        "echo hello",
        execution_id="term-test",
        emit_fn=emit_fn,
    )
    assert result.success
    assert "hello" in result.stdout
    assert "terminal_started" in emitted
    assert "terminal_completed" in emitted


def test_terminal_echo(workspace):
    asyncio.run(_terminal_echo(workspace))


async def _terminal_blocks_destructive(workspace):
    runtime = TerminalRuntime(workspace)
    with pytest.raises(ValueError, match="Destructive"):
        await runtime.execute("rm -rf /")


def test_terminal_blocks_destructive(workspace):
    asyncio.run(_terminal_blocks_destructive(workspace))


async def _terminal_chain(workspace):
    runtime = TerminalRuntime(workspace)
    chain = await runtime.execute_chain([
        ("echo", ["step1"]),
        ("echo", ["step2"]),
    ])
    assert chain.success
    assert len(chain.results) == 2


def test_terminal_chain(workspace):
    asyncio.run(_terminal_chain(workspace))


def test_terminal_history(workspace):
    runtime = TerminalRuntime(workspace)
    assert runtime.get_history() == []
