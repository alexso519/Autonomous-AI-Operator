"""
Tests for policy engine and governance.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_POLICY_ENGINE"] = "1"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


async def _setup_db():
    from app.database.database import get_db

    await get_db()


async def _teardown_db():
    from app.database.database import close_db

    await close_db()


def test_policy_allows_when_disabled():
    os.environ["ENABLE_POLICY_ENGINE"] = "0"

    async def run():
        from app.infrastructure.policy_engine import PolicyEngine

        result = await PolicyEngine.evaluate("exec_p1", "run", tool_name="read_file")
        assert result["allowed"] is True

    asyncio.run(run())


def test_policy_blocks_destructive_tool():
    os.environ["ENABLE_POLICY_ENGINE"] = "1"

    async def run():
        await _setup_db()
        from app.infrastructure.policy_engine import PolicyEngine

        await PolicyEngine.ensure_default_policies()
        result = await PolicyEngine.evaluate(
            "exec_p2", "delete", tool_name="rm_rf", capability="shell"
        )
        assert "violations" in result
        await _teardown_db()

    asyncio.run(run())


def test_compliance_check_local_dev():
    async def run():
        await _setup_db()
        from app.infrastructure.compliance_runtime import ComplianceRuntime

        result = await ComplianceRuntime.run_check("exec_c1", classification="internal")
        assert result["passed"] is True
        assert result["profile"] == "local_dev"
        await _teardown_db()

    asyncio.run(run())
