"""Tests for context compression."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

compression_path = ROOT / "app" / "execution" / "context_compression.py"
spec = importlib.util.spec_from_file_location("context_compression", compression_path)
mod = importlib.util.module_from_spec(spec)
sys.modules["context_compression"] = mod
spec.loader.exec_module(mod)
ContextBudget = mod.ContextBudget
build_compressed_context = mod.build_compressed_context


def test_compresses_older_outputs():
    outputs = {
        f"n{i}": {"agent_name": f"Agent{i}", "output": f"Paragraph {i}. " + "x" * 500, "metadata": {}}
        for i in range(5)
    }
    ctx = build_compressed_context(
        initial_context="Research NVIDIA",
        outputs=outputs,
        tool_calls=[],
        budget=ContextBudget(full_output_count=2, max_total_context_chars=4000),
    )
    assert "Earlier Agent Summaries" in ctx
    assert "Recent Agent Outputs" in ctx
    assert len(ctx) <= 4100


def test_includes_tool_results():
    tool_calls = [
        {
            "status": "completed",
            "tool_name": "web_search",
            "output": {
                "results": [{"title": "NVIDIA", "snippet": "GPU company"}],
            },
        }
    ]
    ctx = build_compressed_context(
        initial_context="task",
        outputs={},
        tool_calls=tool_calls,
    )
    assert "Tool Results" in ctx
    assert "NVIDIA" in ctx


def test_skips_retry_agents_in_context():
    outputs = {
        "a1": {"agent_name": "Research Analyst", "output": "Primary research findings here.", "metadata": {}},
        "a2": {"agent_name": "Retry Research Analyst", "output": "Retry duplicate content.", "metadata": {}},
    }
    ctx = build_compressed_context(
        initial_context="task",
        outputs=outputs,
        tool_calls=[],
    )
    assert "Retry Research Analyst" not in ctx
    assert "Research Analyst" in ctx


if __name__ == "__main__":
    test_compresses_older_outputs()
    test_includes_tool_results()
    test_skips_retry_agents_in_context()
    print("All context compression tests passed.")
