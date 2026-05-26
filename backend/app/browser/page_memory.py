"""
Page memory — store page state, actions, and replay support.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PageSnapshot:
    snapshot_id: str
    url: str
    title: str
    content_excerpt: str
    screenshot_path: str | None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "url": self.url,
            "title": self.title,
            "contentExcerpt": self.content_excerpt[:500],
            "screenshotPath": self.screenshot_path,
            "timestamp": self.timestamp,
        }


@dataclass
class ActionRecord:
    action_id: str
    action_type: str
    target: str
    value: str
    success: bool
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "actionType": self.action_type,
            "target": self.target,
            "value": self.value,
            "success": self.success,
            "timestamp": self.timestamp,
        }


class PageMemory:
    """Track browser session state for replay and provenance."""

    def __init__(self, session_id: str | None = None) -> None:
        self.session_id = session_id or f"session-{uuid.uuid4().hex[:8]}"
        self.snapshots: list[PageSnapshot] = []
        self.actions: list[ActionRecord] = []
        self.current_url: str = ""

    def record_snapshot(self, snapshot: PageSnapshot) -> None:
        self.snapshots.append(snapshot)
        self.current_url = snapshot.url

    def record_action(self, action: ActionRecord) -> None:
        self.actions.append(action)

    def get_replay_timeline(self) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        for snap in self.snapshots:
            timeline.append({"type": "snapshot", **snap.to_dict()})
        for act in self.actions:
            timeline.append({"type": "action", **act.to_dict()})
        timeline.sort(key=lambda x: x.get("timestamp", ""))
        return timeline

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "currentUrl": self.current_url,
            "snapshotCount": len(self.snapshots),
            "actionCount": len(self.actions),
            "timeline": self.get_replay_timeline(),
        }
