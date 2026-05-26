"""
Safety constraints for desktop computer-use actions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


DANGEROUS_KEYWORDS = frozenset({
    "delete", "remove", "format", "shutdown", "power off", "rm -rf",
    "registry", "administrator", "sudo", "password", "credential",
})

RESTRICTED_REGIONS: list[tuple[int, int, int, int]] = []


@dataclass
class ActionSafetyPolicy:
    """Rate limiting, region restrictions, and dangerous-action suppression."""

    max_actions_per_minute: int = 60
    require_confirmation: bool = False
    restricted_regions: list[tuple[int, int, int, int]] = field(default_factory=list)
    masked_regions: list[tuple[int, int, int, int]] = field(default_factory=list)
    dry_run: bool = False

    _action_timestamps: list[float] = field(default_factory=list, repr=False)
    _action_log: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def check_rate_limit(self) -> tuple[bool, str]:
        now = time.monotonic()
        self._action_timestamps = [t for t in self._action_timestamps if now - t < 60]
        if len(self._action_timestamps) >= self.max_actions_per_minute:
            return False, "rate_limit_exceeded"
        return True, ""

    def record_action(self, action: dict[str, Any]) -> None:
        self._action_timestamps.append(time.monotonic())
        self._action_log.append({**action, "recordedAt": time.time()})

    def is_dangerous(self, description: str) -> bool:
        lower = description.lower()
        return any(kw in lower for kw in DANGEROUS_KEYWORDS)

    def is_in_restricted_region(self, x: int, y: int) -> bool:
        regions = self.restricted_regions or RESTRICTED_REGIONS
        for rx, ry, rw, rh in regions:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return True
        return False

    def validate_click(self, x: int, y: int, label: str = "") -> tuple[bool, str]:
        ok, reason = self.check_rate_limit()
        if not ok:
            return False, reason
        if self.is_in_restricted_region(x, y):
            return False, "restricted_region"
        if self.is_dangerous(label):
            return False, "dangerous_action_suppressed"
        if self.require_confirmation:
            return False, "confirmation_required"
        return True, ""

    def get_action_log(self) -> list[dict[str, Any]]:
        return list(self._action_log)
