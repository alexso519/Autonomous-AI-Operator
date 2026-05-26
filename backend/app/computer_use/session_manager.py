"""
Persistent desktop session manager — cross-restart session lifecycle.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

EmitFn = Callable[..., Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PersistentSession:
    session_id: str
    execution_id: str
    mode: str = "desktop"
    status: str = "active"
    active_app: str = ""
    window_state: dict[str, Any] = field(default_factory=dict)
    interaction_count: int = 0
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "executionId": self.execution_id,
            "mode": self.mode,
            "status": self.status,
            "activeApp": self.active_app,
            "windowState": self.window_state,
            "interactionCount": self.interaction_count,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class SessionManager:
    """Persistent desktop sessions with DB backing and in-memory cache."""

    _active: dict[str, PersistentSession] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    async def start(
        self,
        *,
        mode: str = "desktop",
        emit_fn: EmitFn | None = None,
        restore: bool = True,
    ) -> PersistentSession:
        if restore:
            existing = await self._load_latest()
            if existing and existing.status == "active":
                self._active[self.execution_id] = existing
                if emit_fn:
                    await emit_fn(
                        self.execution_id,
                        "desktop_session_restored",
                        "DesktopNavigator",
                        f"Restored session {existing.session_id[:8]}",
                        session=existing.to_dict(),
                    )
                return existing

        session = PersistentSession(
            session_id=f"dsess-{uuid.uuid4().hex[:10]}",
            execution_id=self.execution_id,
            mode=mode,
        )
        self._active[self.execution_id] = session
        await self._persist(session)

        if emit_fn:
            await emit_fn(
                self.execution_id,
                "desktop_session_started",
                "DesktopNavigator",
                f"Desktop session started ({mode})",
                session=session.to_dict(),
            )
        return session

    async def update(
        self,
        *,
        active_app: str | None = None,
        window_state: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> PersistentSession:
        session = self._active.get(self.execution_id)
        if not session:
            session = await self.start(restore=False)
        if active_app is not None:
            session.active_app = active_app
        if window_state is not None:
            session.window_state.update(window_state)
        if status is not None:
            session.status = status
        session.updated_at = _now()
        await self._persist(session)
        return session

    def record_interaction(self) -> None:
        session = self._active.get(self.execution_id)
        if session:
            session.interaction_count += 1

    def get_session(self) -> PersistentSession | None:
        return self._active.get(self.execution_id)

    async def close(self) -> None:
        session = self._active.get(self.execution_id)
        if session:
            session.status = "closed"
            session.updated_at = _now()
            await self._persist(session)
        self._active.pop(self.execution_id, None)

    async def _load_latest(self) -> PersistentSession | None:
        try:
            from app.database.database import get_db

            db = await get_db()
            cursor = await db.execute(
                """SELECT session_id, mode, status, active_app, window_state, session_data,
                          created_at, updated_at
                   FROM desktop_sessions
                   WHERE execution_id = ? AND status = 'active'
                   ORDER BY updated_at DESC LIMIT 1""",
                (self.execution_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            session_data = json.loads(row[5] or "{}")
            return PersistentSession(
                session_id=row[0],
                execution_id=self.execution_id,
                mode=row[1],
                status=row[2],
                active_app=row[3],
                window_state=json.loads(row[4] or "{}"),
                interaction_count=session_data.get("interactionCount", 0),
                created_at=row[6],
                updated_at=row[7],
            )
        except Exception as exc:
            logger.debug("Session load skipped: %s", exc)
            return None

    async def _persist(self, session: PersistentSession) -> None:
        try:
            from app.database.database import get_db

            db = await get_db()
            session_data = {"interactionCount": session.interaction_count}
            await db.execute(
                """INSERT INTO desktop_sessions
                   (id, execution_id, session_id, mode, status, active_app, window_state,
                    session_data, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status,
                     active_app=excluded.active_app,
                     window_state=excluded.window_state,
                     session_data=excluded.session_data,
                     updated_at=excluded.updated_at""",
                (
                    f"ds-{session.session_id}",
                    self.execution_id,
                    session.session_id,
                    session.mode,
                    session.status,
                    session.active_app,
                    json.dumps(session.window_state),
                    json.dumps(session_data),
                    session.created_at,
                    session.updated_at,
                ),
            )
            await db.commit()
        except Exception as exc:
            logger.debug("Session persist skipped: %s", exc)
