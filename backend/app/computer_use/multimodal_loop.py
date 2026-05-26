"""
Multimodal reasoning loop — orchestrates perception-action cycles with reflection.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from app.computer_use.action_planner import ActionPlanner, DesktopPlan
from app.computer_use.desktop_state_tracker import DesktopStateTracker
from app.computer_use.interaction_reflection import InteractionReflection
from app.computer_use.mouse_keyboard_controller import MouseKeyboardController
from app.computer_use.perception_action_cycle import PerceptionActionCycle
from app.computer_use.screen_perception import ScreenPerception
from app.computer_use.ui_navigation_graph import UINavigationGraph
from app.computer_use.visual_grounding import VisualGrounding
from app.config.settings import settings

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class MultimodalLoop:
    """Observe → reason → act loop with adaptive retry and visual context."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.perception = ScreenPerception(execution_id)
        self.grounding = VisualGrounding(self.perception.memory)
        self.controller = MouseKeyboardController(execution_id)
        self.nav_graph = UINavigationGraph()
        self.state_tracker = DesktopStateTracker(execution_id)
        self.reflection = InteractionReflection()
        self.cycle = PerceptionActionCycle(
            execution_id,
            self.perception,
            self.controller,
            self.grounding,
            self.nav_graph,
            self.state_tracker,
            self.reflection,
        )

    async def run(
        self,
        objective: str,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "WorkflowExecutor",
        plan: DesktopPlan | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_computer_use:
            return {"success": False, "error": "computer_use_disabled"}

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "multimodal_reasoning_started",
                agent,
                f"Starting multimodal loop: {objective[:100]}",
                objective=objective[:300],
            )

        await self._run_semantic_pipeline(emit_fn=emit_fn, agent=agent)

        desktop_plan = plan or ActionPlanner.plan(objective)
        results: list[dict[str, Any]] = []
        failures = 0

        for step in desktop_plan.steps:
            try:
                result = await self.cycle.execute_step(step, emit_fn=emit_fn, agent=agent)
                results.append(result.to_dict())
                if not result.success:
                    failures += 1
                    try:
                        from app.computer_use.computer_use_store import save_failed_ui_action
                        await save_failed_ui_action(
                            self.execution_id,
                            action_type=step.action,
                            reason=result.error,
                            recovery_attempted=result.recovered,
                        )
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Cycle step failed: %s", exc)
                failures += 1
                results.append({"step": step.to_dict(), "success": False, "error": str(exc)})

        visual_context = self.perception.memory.to_context_dict()
        state = self.state_tracker.state

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "desktop_state_updated",
                agent,
                f"Desktop state updated — {len(results)} steps, {failures} failures",
                state=state.to_dict(),
                visualContext=visual_context,
            )

        return {
            "success": failures == 0,
            "plan": desktop_plan.to_dict(),
            "results": results,
            "visualContext": visual_context,
            "navigationGraph": self.nav_graph.to_dict(),
            "replayLog": self.controller.get_replay_log(),
            "failures": failures,
        }

    async def _run_semantic_pipeline(
        self,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "VisualAnalyst",
    ) -> dict[str, Any]:
        """Semantic UI intelligence + multimodal reasoning pipeline."""
        from app.computer_use.ui_semantic_parser import UISemanticParser
        from app.computer_use.semantic_ui_memory import SemanticUIMemory
        from app.computer_use.layout_reasoner import LayoutReasoner
        from app.computer_use.interaction_predictor import InteractionPredictor
        from app.computer_use.screen_change_detector import ScreenChangeDetector
        from app.computer_use.attention_manager import AttentionManager
        from app.computer_use.multimodal_reasoner import MultimodalReasoner

        snap = await self.perception.perceive(emit_fn=emit_fn)
        elements = snap.elements if snap else []
        ocr_text = snap.ocr_text if snap else ""

        parser = UISemanticParser()
        semantic = await parser.parse_and_emit(
            self.execution_id, elements, ocr_text=ocr_text, emit_fn=emit_fn, agent=agent,
        )

        layout_reasoner = LayoutReasoner()
        layout = await layout_reasoner.reason_and_emit(
            self.execution_id,
            [e.to_dict() for e in semantic.elements],
            emit_fn=emit_fn,
            agent=agent,
        )

        sem_memory = SemanticUIMemory(self.execution_id)
        labels = [e.label for e in semantic.elements]
        sem_memory.record(semantic.dominant_type, labels, len(semantic.elements))

        predictor = InteractionPredictor()
        prediction = await predictor.predict_and_emit(
            self.execution_id,
            semantic.to_dict(),
            layout.to_dict(),
            semantic_memory=sem_memory,
            emit_fn=emit_fn,
            agent=agent,
        )

        change_detector = ScreenChangeDetector(self.execution_id)
        change = await change_detector.detect_and_emit(
            ocr_text=ocr_text,
            element_count=len(elements),
            emit_fn=emit_fn,
            agent=agent,
        )

        attention_mgr = AttentionManager(self.execution_id)
        regions = await attention_mgr.shift_attention(
            [e.to_dict() for e in semantic.elements],
            predictions=[p.to_dict() for p in prediction.predictions],
            emit_fn=emit_fn,
            agent=agent,
        )

        reasoner = MultimodalReasoner(self.execution_id)
        context = await reasoner.reason_and_emit(
            ocr_text=ocr_text,
            semantic_ui=semantic.to_dict(),
            layout=layout.to_dict(),
            attention=[r.to_dict() for r in regions],
            screen_changes=change.to_dict(),
            emit_fn=emit_fn,
            agent=agent,
        )

        self.nav_graph.record_transition(
            change.previous_hash or "start",
            change.current_hash or "current",
            "semantic_perceive",
        )

        return {
            "semanticUi": semantic.to_dict(),
            "layout": layout.to_dict(),
            "prediction": prediction.to_dict(),
            "change": change.to_dict(),
            "context": context.to_dict(),
            "snapshot": snap.to_dict() if snap and hasattr(snap, "to_dict") else {},
        }

    def get_visual_context_for_cognition(self) -> dict[str, Any]:
        """Inject visual state into cognitive/deliberation context."""
        return {
            "desktopState": self.state_tracker.state.to_dict(),
            "visualMemory": self.perception.memory.to_context_dict(),
            "navigationGraph": self.nav_graph.to_dict(),
        }

    async def cleanup(self) -> None:
        DesktopStateTracker.cleanup(self.execution_id)
