"""
Tests for RBAC and authentication.
Run with: python -m pytest backend/tests/test_rbac.py -v
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_RBAC"] = "0"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


def test_rbac_local_dev_allows_all():
    from app.infrastructure.auth_manager import AuthContext
    from app.infrastructure.rbac import RBAC, Permission

    ctx = AuthContext(user_id="local", roles=["admin", "operator", "viewer"])
    assert RBAC.has_permission(ctx, Permission.EXECUTE_WORKFLOW)


def test_rbac_viewer_restricted():
    from app.infrastructure.auth_manager import AuthContext
    from app.infrastructure.rbac import RBAC, Permission

    ctx = AuthContext(user_id="u1", roles=["viewer"])
    assert RBAC.has_permission(ctx, Permission.VIEW_EXECUTION)
    assert not RBAC.has_permission(ctx, Permission.EXECUTE_WORKFLOW)


def test_auth_local_mode():
    async def run():
        from app.database.database import get_db, close_db

        await get_db()
        from app.infrastructure.auth_manager import AuthManager

        ctx = await AuthManager.authenticate()
        assert ctx is not None
        assert ctx.auth_method == "local_dev"
        await close_db()

    asyncio.run(run())


def test_tenant_quota_default():
    async def run():
        from app.database.database import get_db, close_db

        await get_db()
        from app.infrastructure.tenant_runtime import TenantRuntime

        quota = await TenantRuntime.check_quota("default")
        assert quota["allowed"] is True
        await close_db()

    asyncio.run(run())
