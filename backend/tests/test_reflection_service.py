import importlib.util
import sys
from pathlib import Path
from types import ModuleType

# Provide minimal stubs to satisfy imports when full dependencies are unavailable.
crewai = ModuleType("crewai")
crewai.Agent = type("Agent", (), {})
crewai.Task = type("Task", (), {})
crewai.agent = ModuleType("crewai.agent")
crewai.agent.Agent = crewai.Agent
crewai.agents = ModuleType("crewai.agents")
crewai.memory = ModuleType("crewai.memory")
crewai.memory.entity = ModuleType("crewai.memory.entity")
crewai.memory.entity.entity_memory_item = ModuleType("crewai.memory.entity.entity_memory_item")
crewai.memory.storage = ModuleType("crewai.memory.storage")
crewai.memory.storage.rag_storage = ModuleType("crewai.memory.storage.rag_storage")
crewai.memory.storage.rag_storage.App = type("App", (), {})
sys.modules["crewai"] = crewai
sys.modules["crewai.agent"] = crewai.agent
sys.modules["crewai.agents"] = crewai.agents
sys.modules["crewai.memory"] = crewai.memory
sys.modules["crewai.memory.entity"] = crewai.memory.entity
sys.modules["crewai.memory.entity.entity_memory_item"] = crewai.memory.entity.entity_memory_item
sys.modules["crewai.memory.storage"] = crewai.memory.storage
sys.modules["crewai.memory.storage.rag_storage"] = crewai.memory.storage.rag_storage

chat_models = ModuleType("langchain_community.chat_models")
chat_models.ChatOllama = type("ChatOllama", (), {})
langchain_community = ModuleType("langchain_community")
langchain_community.chat_models = chat_models
sys.modules["langchain_community"] = langchain_community
sys.modules["langchain_community.chat_models"] = chat_models

module_path = Path(__file__).resolve().parents[1] / "app" / "execution" / "reflection_service.py"
sys.path.insert(0, str(module_path.parents[2]))
spec = importlib.util.spec_from_file_location("reflection_service", module_path)
reflection_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reflection_service)
ReflectionService = reflection_service.ReflectionService
ReplanningService = reflection_service.ReplanningService

cm_path = Path(__file__).resolve().parents[1] / "app" / "execution" / "context_manager.py"
cm_spec = importlib.util.spec_from_file_location("context_manager", cm_path)
context_manager = importlib.util.module_from_spec(cm_spec)
cm_spec.loader.exec_module(context_manager)
ContextManager = context_manager.ContextManager

import unittest


class ReflectionServiceTests(unittest.TestCase):

    def test_inspect_output_detects_low_quality_and_trigger_replanning(self) -> None:
        ctx = ContextManager()
        ctx.add_output("agent-0", "Planner Agent", "Too short.")
        node = {
            "id": "agent-0",
            "data": {
                "label": "Planner Agent",
                "goal": "Create a detailed strategy and roadmap for execution",
            },
        }

        finding = ReflectionService.inspect_output(node, ctx)

        self.assertTrue(finding.should_replan)
        self.assertIn("low_word_count", finding.issues)
        self.assertIn("missing_section", finding.issues)
        self.assertIn(finding.action, ["validate_then_retry", "retry_with_validation", "retry"])

    def test_replanning_inserts_recovery_nodes_after_failed_reflection(self) -> None:
        ctx = ContextManager()
        ctx.add_output("agent-0", "Planner Agent", "Too short.")
        node = {
            "id": "agent-0",
            "data": {
                "label": "Planner Agent",
                "goal": "Create a detailed strategy and roadmap for execution",
            },
            "position": {"x": 200, "y": 100},
        }
        next_node = {
            "id": "agent-1",
            "data": {
                "label": "Writer Agent",
                "goal": "Draft the full plan document",
            },
            "position": {"x": 200, "y": 280},
        }
        sorted_nodes = [node, next_node]
        edges: list[dict[str, str]] = []

        finding = ReflectionService.inspect_output(node, ctx)
        new_nodes = ReplanningService.insert_recovery_steps(
            sorted_nodes=sorted_nodes,
            edges=edges,
            node_index=0,
            node=node,
            finding=finding,
            ctx=ctx,
        )

        self.assertGreater(len(new_nodes), 2)
        self.assertGreaterEqual(len(edges), 1)
        self.assertTrue(any("retry" in n["id"] or "validation" in n["id"] for n in new_nodes if n["id"] != "agent-0"))

    def test_reflection_respects_max_retry_limit(self) -> None:
        ctx = ContextManager()
        ctx.set_workflow_memory("reflection_retry_counts", {"agent-0": 2})
        ctx.add_output("agent-0", "Planner Agent", "Too short.")
        node = {
            "id": "agent-0",
            "data": {
                "label": "Planner Agent",
                "goal": "Create a detailed strategy and roadmap for execution",
            },
        }

        finding = ReflectionService.inspect_output(node, ctx)
        self.assertFalse(finding.should_replan)
        self.assertEqual(finding.action, "max_retries_reached")


if __name__ == "__main__":
    unittest.main()
