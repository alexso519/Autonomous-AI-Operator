import importlib.util
import sys
from pathlib import Path

module_path = Path(__file__).resolve().parents[1] / "app" / "execution" / "context_manager.py"
sys.path.insert(0, str(module_path.parents[2]))
spec = importlib.util.spec_from_file_location("context_manager", module_path)
context_manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context_manager)
ContextManager = context_manager.ContextManager


import unittest


class ContextManagerTests(unittest.TestCase):

    def test_context_manager_state_roundtrip(self) -> None:
        ctx = ContextManager()
        ctx.set_context("Workflow objective: test shared memory")
        ctx.set_workflow_memory("objective", "Analyze the task")
        ctx.add_output("node_1", "Research Agent", "Found useful insight.")
        ctx.add_node_memory("node_1", "artifact", {"type": "note", "text": "Important detail"})

        state = ctx.get_state()
        self.assertEqual(state["initial_context"], "Workflow objective: test shared memory")
        self.assertEqual(state["workflow_memory"]["objective"], "Analyze the task")
        self.assertEqual(state["outputs"]["node_1"]["output"], "Found useful insight.")
        self.assertEqual(state["node_memory"]["node_1"]["artifact"]["type"], "note")

        restored = ContextManager()
        restored.restore_state(state)
        self.assertEqual(restored.get_initial_context(), ctx.get_initial_context())
        self.assertEqual(restored.get_workflow_memory("objective"), "Analyze the task")
        self.assertEqual(restored.get_all_outputs()["node_1"], "Found useful insight.")
        self.assertEqual(restored.get_node_memory("node_1")["artifact"]["text"], "Important detail")

    def test_combined_context_includes_workflow_memory_and_outputs(self) -> None:
        ctx = ContextManager()
        ctx.set_context("Start here")
        ctx.set_workflow_memory("findings", "Key insight")
        ctx.add_output("node_2", "Writer Agent", "Drafted a plan.")

        combined = ctx.get_combined_context()
        self.assertIn("Workflow Context:\nStart here", combined)
        self.assertIn("- findings: Key insight", combined)
        self.assertIn("[Writer Agent]: Drafted a plan.", combined)
