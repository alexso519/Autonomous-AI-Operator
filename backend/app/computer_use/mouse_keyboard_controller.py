"""
Mouse and keyboard controller with safety, rate limiting, and replay logging.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.computer_use.safety import ActionSafetyPolicy
from app.config.settings import settings

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]

_pyautogui_available = False
try:
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _pyautogui_available = True
except ImportError:
    pyautogui = None  # type: ignore


@dataclass
class InputActionResult:
    action_id: str
    action_type: str
    success: bool
    coordinates: tuple[int, int] | None = None
    value: str = ""
    error: str | None = None
    dry_run: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "actionType": self.action_type,
            "success": self.success,
            "coordinates": list(self.coordinates) if self.coordinates else None,
            "value": self.value,
            "error": self.error,
            "dryRun": self.dry_run,
            "durationMs": self.duration_ms,
        }


class MouseKeyboardController:
    """Execute mouse/keyboard actions with throttling and replay logging."""

    def __init__(
        self,
        execution_id: str,
        *,
        policy: ActionSafetyPolicy | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.execution_id = execution_id
        self.policy = policy or ActionSafetyPolicy(
            dry_run=dry_run if dry_run is not None else settings.computer_use_headless,
        )
        self._last_action_at: float = 0.0
        self._min_interval = 1.0 / max(settings.computer_use_max_actions_per_minute / 60, 0.1)

    @property
    def can_control(self) -> bool:
        return _pyautogui_available and not self.policy.dry_run

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_action_at
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_action_at = time.monotonic()

    async def move_to(self, x: int, y: int) -> InputActionResult:
        action_id = f"mk-{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        ok, reason = self.policy.validate_click(x, y, f"move_to_{x}_{y}")
        if not ok:
            return InputActionResult(action_id, "move", False, (x, y), error=reason)

        await self._throttle()
        if self.can_control:
            pyautogui.moveTo(x, y, duration=0.15)
        self.policy.record_action({"type": "move", "x": x, "y": y})
        return InputActionResult(
            action_id, "move", True, (x, y),
            dry_run=not self.can_control,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def click(
        self,
        x: int,
        y: int,
        *,
        label: str = "",
        emit_fn: EmitFn | None = None,
        agent: str = "UIOperator",
    ) -> InputActionResult:
        action_id = f"mk-{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        ok, reason = self.policy.validate_click(x, y, label)
        if not ok:
            result = InputActionResult(action_id, "click", False, (x, y), error=reason)
            if emit_fn:
                await emit_fn(
                    self.execution_id, "ui_navigation_failed", agent,
                    f"Click blocked: {reason}",
                    **result.to_dict(),
                )
            return result

        if emit_fn:
            await emit_fn(
                self.execution_id, "mouse_action_planned", agent,
                f"Click at ({x}, {y})" + (f" — {label}" if label else ""),
                actionId=action_id,
                actionType="click",
                x=x, y=y,
                label=label,
            )

        await self._throttle()
        await self.move_to(x, y)
        if self.can_control:
            pyautogui.click(x, y)
        self.policy.record_action({"type": "click", "x": x, "y": y, "label": label})

        result = InputActionResult(
            action_id, "click", True, (x, y),
            dry_run=not self.can_control,
            duration_ms=(time.monotonic() - start) * 1000,
        )
        if emit_fn:
            await emit_fn(
                self.execution_id, "keyboard_action_executed", agent,
                f"Clicked ({x}, {y})",
                **result.to_dict(),
            )
        return result

    async def type_text(
        self,
        text: str,
        *,
        emit_fn: EmitFn | None = None,
        agent: str = "UIOperator",
    ) -> InputActionResult:
        action_id = f"mk-{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        ok, reason = self.policy.check_rate_limit()
        if not ok:
            return InputActionResult(action_id, "type", False, value=text, error=reason)
        if self.policy.is_dangerous(text):
            return InputActionResult(action_id, "type", False, value="", error="dangerous_action_suppressed")

        if emit_fn:
            await emit_fn(
                self.execution_id, "mouse_action_planned", agent,
                f"Type text ({len(text)} chars)",
                actionId=action_id,
                actionType="type",
                length=len(text),
            )

        await self._throttle()
        if self.can_control:
            pyautogui.write(text, interval=0.02)
        self.policy.record_action({"type": "type", "length": len(text)})

        result = InputActionResult(
            action_id, "type", True, value=text[:50],
            dry_run=not self.can_control,
            duration_ms=(time.monotonic() - start) * 1000,
        )
        if emit_fn:
            await emit_fn(
                self.execution_id, "keyboard_action_executed", agent,
                "Keyboard input executed",
                **result.to_dict(),
            )
        return result

    async def scroll(
        self,
        direction: str = "down",
        *,
        amount: int = 3,
        emit_fn: EmitFn | None = None,
        agent: str = "UIOperator",
    ) -> InputActionResult:
        action_id = f"mk-{uuid.uuid4().hex[:8]}"
        start = time.monotonic()
        clicks = -amount if direction == "down" else amount

        await self._throttle()
        if self.can_control:
            pyautogui.scroll(clicks)
        self.policy.record_action({"type": "scroll", "direction": direction, "amount": amount})

        result = InputActionResult(
            action_id, "scroll", True,
            dry_run=not self.can_control,
            duration_ms=(time.monotonic() - start) * 1000,
        )
        if emit_fn:
            await emit_fn(
                self.execution_id, "keyboard_action_executed", agent,
                f"Scrolled {direction}",
                **result.to_dict(),
            )
        return result

    def get_replay_log(self) -> list[dict[str, Any]]:
        return self.policy.get_action_log()
