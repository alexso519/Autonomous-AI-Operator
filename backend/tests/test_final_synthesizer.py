"""Tests for final response synthesizer."""

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

cm_path = ROOT / "app" / "execution" / "context_manager.py"
cm_spec = importlib.util.spec_from_file_location("context_manager", cm_path)
cm_mod = importlib.util.module_from_spec(cm_spec)
sys.modules["context_manager"] = cm_mod
cm_spec.loader.exec_module(cm_mod)
ContextManager = cm_mod.ContextManager

# Stub package hierarchy so final_synthesizer can import without loading engine/crewai
app_pkg = types.ModuleType("app")
exec_pkg = types.ModuleType("app.execution")
exec_pkg.context_manager = cm_mod
app_pkg.execution = exec_pkg
sys.modules.setdefault("app", app_pkg)
sys.modules.setdefault("app.execution", exec_pkg)
sys.modules.setdefault("app.execution.context_manager", cm_mod)

fs_path = ROOT / "app" / "execution" / "final_synthesizer.py"
fs_spec = importlib.util.spec_from_file_location("final_synthesizer", fs_path)
fs_mod = importlib.util.module_from_spec(fs_spec)
sys.modules["final_synthesizer"] = fs_mod
fs_spec.loader.exec_module(fs_mod)
FinalResponseSynthesizer = fs_mod.FinalResponseSynthesizer


def test_synthesizer_produces_markdown_report():
    ctx = ContextManager()
    ctx.set_workflow_memory(
        "tool_calls",
        [
            {
                "status": "completed",
                "tool_name": "web_search",
                "output": {
                    "results": [{"title": "Blackwell", "snippet": "New GPU architecture"}],
                },
            }
        ],
    )
    steps = [
        {
            "agentName": "Research Analyst",
            "status": "completed",
            "output": "## Findings\nNVIDIA Blackwell is a new architecture.",
        },
        {
            "agentName": "Content Writer",
            "status": "completed",
            "output": "## Summary\n- Strong growth\n- AI leadership",
        },
        {
            "agentName": "Retry Content Writer",
            "status": "completed",
            "output": "duplicate retry content",
        },
    ]

    result = FinalResponseSynthesizer.synthesize(
        workflow_name="Auto: research",
        objective="Research NVIDIA Blackwell",
        steps=steps,
        ctx=ctx,
    )

    assert "# Final Report" in result.markdown
    assert "Executive Summary" in result.markdown
    assert "Recommendations" in result.markdown
    assert "Research & Tool Evidence" in result.markdown
    assert "Blackwell" in result.markdown
    assert result.tool_count == 1
    assert result.retry_count == 1


if __name__ == "__main__":
    test_synthesizer_produces_markdown_report()
    print("Final synthesizer tests passed.")
