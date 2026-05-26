"""
Tests for runtime metering and budgets.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_COST_METERING"] = "1"
os.environ["DEFAULT_EXECUTION_BUDGET"] = "1000"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


async def _setup_db():
    from app.database.database import get_db

    await get_db()


async def _teardown_db():
    from app.database.database import close_db

    await close_db()


def test_cost_estimate():
    from app.infrastructure.cost_tracker import CostTracker

    cost = CostTracker.estimate(model_tokens=1000, tool_calls=5)
    assert cost > 0


def test_budget_enforcer_allows_under_limit():
    async def run():
        await _setup_db()
        from app.infrastructure.budget_enforcer import BudgetEnforcer

        result = await BudgetEnforcer.check_and_charge("exec_m1", 1.0, tenant_id="default")
        assert result.get("allowed", True) is True
        await _teardown_db()

    asyncio.run(run())


def test_metering_disabled_no_op():
    async def run():
        from app.config.settings import settings
        from app.infrastructure.runtime_metering import RuntimeMetering

        old = settings.enable_cost_metering
        settings.enable_cost_metering = False
        try:
            await RuntimeMetering.get_instance().on_model_invocation("exec_m2", tokens=100)
            result = await RuntimeMetering.get_instance().on_execution_complete("exec_m2")
            assert result == {}
        finally:
            settings.enable_cost_metering = old

    asyncio.run(run())
