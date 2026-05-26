"""
Runtime schema migration management.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MIGRATION_VERSION = "enterprise_phase2_v1"


class RuntimeMigrations:
    """Applies idempotent runtime schema migrations."""

    @staticmethod
    async def apply_pending() -> dict[str, Any]:
        try:
            from app.database.database import get_db, _init_tables

            conn = await get_db()
            await _init_tables(conn)
            return {"applied": True, "version": MIGRATION_VERSION}
        except Exception as exc:
            logger.warning("Runtime migrations degraded: %s", exc)
            return {"applied": False, "degraded": True}

    @staticmethod
    async def status() -> dict[str, Any]:
        return {"currentVersion": MIGRATION_VERSION, "pending": []}
