"""
Tests for infrastructure intelligence layer.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_INFRA_INTELLIGENCE"] = "1"
os.environ["ENABLE_DISTRIBUTED_RUNTIME"] = "0"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


def test_anomaly_retry_storm():
    os.environ["ENABLE_INFRA_INTELLIGENCE"] = "1"

    async def run():
        from app.infrastructure.anomaly_detector import AnomalyDetector

        result = None
        for _ in range(6):
            result = await AnomalyDetector.record_retry("exec_intel_1")
        assert result is not None
        assert result.get("anomalyType") == "retry_storm"

    asyncio.run(run())


def test_intelligence_disabled():
    async def run():
        from app.config.settings import settings
        from app.infrastructure.infrastructure_intelligence import InfrastructureIntelligence

        old = settings.enable_infra_intelligence
        settings.enable_infra_intelligence = False
        try:
            result = await InfrastructureIntelligence.get_instance().maintenance_cycle()
            assert result.get("enabled") is False
        finally:
            settings.enable_infra_intelligence = old

    asyncio.run(run())


def test_worker_reputation_scoring():
    from app.infrastructure.worker_reputation import WorkerReputation

    WorkerReputation.record_outcome("w1", success=True)
    WorkerReputation.record_outcome("w1", success=False)
    score = WorkerReputation.get_score("w1")
    assert 0 <= score <= 1
