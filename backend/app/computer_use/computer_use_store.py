"""
SQLite persistence for computer-use visual state and actions.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def save_screen_snapshot(
    execution_id: str,
    *,
    snapshot_id: str | None = None,
    image_path: str = "",
    width: int = 0,
    height: int = 0,
    fingerprint: str = "",
    metadata: dict[str, Any] | None = None,
) -> str:
    from app.database.database import get_db

    sid = snapshot_id or f"snap-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO screen_snapshots
           (id, execution_id, image_path, width, height, fingerprint, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (sid, execution_id, image_path, width, height, fingerprint,
         json.dumps(metadata or {}), _now()),
    )
    await db.commit()
    return sid


async def save_visual_element(
    execution_id: str,
    snapshot_id: str,
    *,
    element_id: str | None = None,
    element_type: str = "unknown",
    label: str = "",
    bbox: list[int] | None = None,
    confidence: float = 0.0,
    interactive: bool = False,
    metadata: dict[str, Any] | None = None,
) -> str:
    from app.database.database import get_db

    eid = element_id or f"vel-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO visual_elements
           (id, execution_id, snapshot_id, element_type, label, bbox_json,
            confidence, interactive, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (eid, execution_id, snapshot_id, element_type, label,
         json.dumps(bbox or []), confidence, int(interactive),
         json.dumps(metadata or {}), _now()),
    )
    await db.commit()
    return eid


async def save_ocr_extraction(
    execution_id: str,
    snapshot_id: str,
    *,
    engine: str,
    text: str,
    regions: list[dict[str, Any]] | None = None,
) -> str:
    from app.database.database import get_db

    oid = f"ocr-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO ocr_extractions
           (id, execution_id, snapshot_id, engine, text_content, regions_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (oid, execution_id, snapshot_id, engine, text,
         json.dumps(regions or []), _now()),
    )
    await db.commit()
    return oid


async def save_ui_state(
    execution_id: str,
    *,
    snapshot_id: str = "",
    state_hash: str = "",
    change_detected: bool = False,
    state_data: dict[str, Any] | None = None,
) -> str:
    from app.database.database import get_db

    hid = f"uist-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO ui_state_history
           (id, execution_id, snapshot_id, state_hash, change_detected, state_data, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (hid, execution_id, snapshot_id, state_hash, int(change_detected),
         json.dumps(state_data or {}), _now()),
    )
    await db.commit()
    return hid


async def save_planned_action(
    execution_id: str,
    *,
    action_type: str,
    target: str = "",
    coordinates: list[int] | None = None,
    confidence: float = 0.0,
    plan_data: dict[str, Any] | None = None,
) -> str:
    from app.database.database import get_db

    pid = f"plan-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO planned_actions
           (id, execution_id, action_type, target, coordinates_json,
            confidence, plan_data, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)""",
        (pid, execution_id, action_type, target, json.dumps(coordinates or []),
         confidence, json.dumps(plan_data or {}), _now()),
    )
    await db.commit()
    return pid


async def save_executed_action(
    execution_id: str,
    planned_action_id: str,
    *,
    success: bool,
    result_data: dict[str, Any] | None = None,
    error: str = "",
) -> str:
    from app.database.database import get_db

    eid = f"exec-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO executed_actions
           (id, execution_id, planned_action_id, success, result_data, error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (eid, execution_id, planned_action_id, int(success),
         json.dumps(result_data or {}), error, _now()),
    )
    await db.commit()
    return eid


async def save_failed_ui_action(
    execution_id: str,
    *,
    action_type: str,
    reason: str,
    recovery_attempted: bool = False,
    metadata: dict[str, Any] | None = None,
) -> str:
    from app.database.database import get_db

    fid = f"fail-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO failed_ui_actions
           (id, execution_id, action_type, reason, recovery_attempted, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (fid, execution_id, action_type, reason, int(recovery_attempted),
         json.dumps(metadata or {}), _now()),
    )
    await db.commit()
    return fid


async def save_navigation_path(
    execution_id: str,
    *,
    path_nodes: list[str],
    path_data: dict[str, Any] | None = None,
) -> str:
    from app.database.database import get_db

    nid = f"nav-{uuid.uuid4().hex[:12]}"
    db = await get_db()
    await db.execute(
        """INSERT INTO ui_navigation_paths
           (id, execution_id, path_nodes, path_data, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (nid, execution_id, json.dumps(path_nodes), json.dumps(path_data or {}), _now()),
    )
    await db.commit()
    return nid
