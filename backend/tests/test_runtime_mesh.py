"""
Tests for federated runtime mesh.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["ENABLE_RUNTIME_MESH"] = "1"
os.environ["ENABLE_DISTRIBUTED_RUNTIME"] = "0"
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")


async def _setup_db():
    from app.database.database import get_db

    await get_db()


async def _teardown_db():
    from app.database.database import close_db

    await close_db()


def test_local_topology_when_mesh_disabled():
    os.environ["ENABLE_RUNTIME_MESH"] = "0"

    async def run():
        from app.infrastructure.runtime_mesh import RuntimeMesh

        topo = await RuntimeMesh.get_instance().get_topology()
        assert topo["mode"] == "local"
        assert len(topo["nodes"]) >= 1

    asyncio.run(run())


def test_mesh_initialize_and_topology():
    os.environ["ENABLE_RUNTIME_MESH"] = "1"

    async def run():
        await _setup_db()
        from app.infrastructure.runtime_mesh import RuntimeMesh

        result = await RuntimeMesh.get_instance().initialize()
        assert result.get("mode") in ("federation", "hybrid", "distributed_cluster", "local")
        topo = await RuntimeMesh.get_instance().get_topology()
        assert "healthScore" in topo
        await _teardown_db()

    asyncio.run(run())


def test_remote_execution_router_local_fallback():
    os.environ["ENABLE_RUNTIME_MESH"] = "0"

    async def run():
        await _setup_db()
        from app.infrastructure.remote_execution_router import RemoteExecutionRouter

        route = await RemoteExecutionRouter.route_execution("exec_mesh_1", "wf_1")
        assert route["mode"] == "local"
        await _teardown_db()

    asyncio.run(run())
