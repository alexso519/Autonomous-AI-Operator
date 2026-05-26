"""
Recovery executor — intelligent UI action retry and adaptive recovery.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


class RecoveryExecutor:
    """Retry failed UI actions with re-grounding and reflection."""

    _attempt_counts: dict[str, int] = {}
    MAX_ATTEMPTS = 3

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    async def attempt_recovery(
        self,
        action_type: str,
        target: str,
        error: str,
        *,
        emit_fn: EmitFn | None = None,
    ) -> dict[str, Any]:
        key = f"{action_type}:{target}"
        count = self._attempt_counts.get(key, 0) + 1
        self._attempt_counts[key] = count

        if count > self.MAX_ATTEMPTS:
            return {"recovered": False, "reason": "max_attempts_exceeded", "attempts": count}

        strategy = self._select_strategy(action_type, error, count)

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "ui_recovery_attempted",
                "UIOperator",
                f"Recovery attempt {count}/{self.MAX_ATTEMPTS}: {strategy}",
                actionType=action_type,
                target=target[:80],
                strategy=strategy,
                attempt=count,
                error=error[:200],
            )

        recovered = await self._execute_strategy(strategy, action_type, target, emit_fn=emit_fn)
        return {"recovered": recovered, "strategy": strategy, "attempts": count}

    def _select_strategy(self, action_type: str, error: str, attempt: int) -> str:
        err_lower = error.lower()
        if "not found" in err_lower or "ground" in err_lower:
            return "re_ground"
        if "timeout" in err_lower or "stall" in err_lower:
            return "wait_and_retry"
        if action_type == "click" and attempt >= 2:
            return "alternative_target"
        if attempt == 1:
            return "re_perceive"
        return "reflection_retry"

    async def _execute_strategy(
        self,
        strategy: str,
        action_type: str,
        target: str,
        *,
        emit_fn: EmitFn | None = None,
    ) -> bool:
        try:
            if strategy == "re_ground":
                from app.computer_use.visual_grounding import VisualGrounding
                from app.computer_use.screen_perception import ScreenPerception
                perception = ScreenPerception(self.execution_id)
                await perception.perceive(emit_fn=emit_fn)
                grounding = VisualGrounding(perception.memory)
                result = grounding.ground(target)
                return result.confidence > 0.3

            if strategy == "re_perceive":
                from app.computer_use.screen_perception import ScreenPerception
                perception = ScreenPerception(self.execution_id)
                snap = await perception.perceive(emit_fn=emit_fn)
                return snap is not None

            if strategy == "wait_and_retry":
                import asyncio
                await asyncio.sleep(1.0)
                return True

            if strategy == "reflection_retry":
                from app.computer_use.interaction_reflection import InteractionReflection
                reflection = InteractionReflection()
                advice = reflection.reflect(action_type, target, "recovery")
                return advice.should_retry

            if strategy == "alternative_target":
                alt = target.replace("OK", "Ok").replace("ok", "OK")
                return alt != target

        except Exception as exc:
            logger.warning("Recovery strategy %s failed: %s", strategy, exc)
        return False

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        keys = [k for k in cls._attempt_counts if execution_id in k]
        for k in keys:
            cls._attempt_counts.pop(k, None)
