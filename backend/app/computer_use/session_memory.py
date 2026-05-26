"""
Session memory — semantic desktop continuity across interactions.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MemoryEntry:
    entry_type: str
    content: dict[str, Any]
    timestamp: str = field(default_factory=_now)
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entryType": self.entry_type,
            "content": self.content,
            "timestamp": self.timestamp,
            "fingerprint": self.fingerprint,
        }


class SessionMemory:
    """In-memory semantic session memory with UI continuity tracking."""

    _stores: dict[str, list[MemoryEntry]] = {}

    def __init__(self, execution_id: str, session_id: str = "") -> None:
        self.execution_id = execution_id
        self.session_id = session_id
        if execution_id not in self._stores:
            self._stores[execution_id] = []

    @property
    def entries(self) -> list[MemoryEntry]:
        return self._stores.get(self.execution_id, [])

    def remember(
        self,
        entry_type: str,
        content: dict[str, Any],
        *,
        fingerprint_source: str = "",
    ) -> MemoryEntry:
        fp = ""
        if fingerprint_source:
            fp = hashlib.sha256(fingerprint_source.encode()).hexdigest()[:16]
        entry = MemoryEntry(entry_type=entry_type, content=content, fingerprint=fp)
        self._stores.setdefault(self.execution_id, []).append(entry)
        return entry

    def recall(self, entry_type: str | None = None, limit: int = 20) -> list[MemoryEntry]:
        items = self.entries
        if entry_type:
            items = [e for e in items if e.entry_type == entry_type]
        return items[-limit:]

    def last_ui_fingerprint(self) -> str:
        for entry in reversed(self.entries):
            if entry.entry_type == "ui_state" and entry.fingerprint:
                return entry.fingerprint
        return ""

    def continuity_context(self) -> dict[str, Any]:
        recent = self.recall(limit=10)
        return {
            "executionId": self.execution_id,
            "sessionId": self.session_id,
            "entryCount": len(self.entries),
            "recentEntries": [e.to_dict() for e in recent],
            "lastUiFingerprint": self.last_ui_fingerprint(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "executionId": self.execution_id,
            "sessionId": self.session_id,
            "entries": [e.to_dict() for e in self.entries[-50:]],
        }

    def export_for_replay(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._stores.pop(execution_id, None)
