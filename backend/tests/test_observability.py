"""
Tests for observability, tracing, and metrics.
Run with: python -m pytest backend/tests/test_observability.py -v
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_DISTRIBUTED_RUNTIME"] = "0"
os.environ["ENABLE_OTEL_TRACING"] = "0"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


def test_metrics_aggregator():
    from app.infrastructure.metrics_aggregator import MetricsAggregator

    MetricsAggregator.reset()
    MetricsAggregator.increment("test.counter")
    text = MetricsAggregator.prometheus_text()
    assert "test_counter" in text
    MetricsAggregator.reset()


def test_metrics_aggregate():
    async def run():
        from app.database.database import get_db, close_db
        from app.infrastructure.metrics_aggregator import MetricsAggregator

        MetricsAggregator.reset()
        await get_db()
        MetricsAggregator.increment("executions.started")
        snapshot = await MetricsAggregator.aggregate()
        assert snapshot.counters.get("executions.started") == 1.0
        await close_db()
        MetricsAggregator.reset()

    asyncio.run(run())


def test_distributed_tracing():
    async def run():
        from app.infrastructure.distributed_tracing import DistributedTracing

        span = DistributedTracing.start_span("test_action", execution_id="exec_t1")
        await DistributedTracing.end_span(span)
        assert len(DistributedTracing.get_recent_spans(limit=5)) >= 1

    asyncio.run(run())


def test_runtime_observability_health():
    async def run():
        from app.database.database import get_db, close_db
        from app.infrastructure.runtime_observability import RuntimeObservability

        await get_db()
        summary = await RuntimeObservability.collect_health_summary()
        assert summary.status in ("healthy", "degraded")
        await close_db()

    asyncio.run(run())


def test_deployment_validation():
    async def run():
        from app.database.database import get_db, close_db
        from app.infrastructure.runtime_config_validator import RuntimeConfigValidator
        from app.infrastructure.deployment_profiles import DeploymentProfiles

        await get_db()
        report = RuntimeConfigValidator.validate()
        assert "valid" in report.to_dict()
        assert "profile" in DeploymentProfiles.get_capabilities()
        await close_db()

    asyncio.run(run())
