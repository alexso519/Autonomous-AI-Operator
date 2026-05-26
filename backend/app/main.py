"""
FastAPI application entry point.

Startup lifecycle:
  1. Logging configured
  2. CORS middleware attached
  3. Database initialized (tables created)
  4. Orphaned execution recovery (crash recovery)
  5. All route modules registered
  6. Orphan SSE queue sweep started

Shutdown lifecycle:
  1. Cancel all running executions
  2. Clean up SSE queues
  3. Close database connection

Request flow:
  Browser → FastAPI (port 8000) → CORS check → Route handler → DB/Ollama → Response
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.database.database import get_db, close_db
from app.execution.manager import execution_manager, recover_orphaned_executions
from app.streaming.event_manager import event_manager
from app.routes import register_routes


# ── Logging ─────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Application Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager.

    Startup:
    - Initializes SQLite database (creates file + tables if missing)
    - Recovers orphaned executions from previous crashes
    - Registers all route modules
    - Starts orphan SSE queue sweep

    Shutdown:
    - Cancels all running executions via ExecutionManager
    - Cleans up all SSE event queues
    - Shuts down agent thread pool
    - Closes database connection gracefully
    """
    logger.info("Starting Personal AI Agent Workspace backend...")

    # Initialize database on startup
    try:
        await get_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Database initialization failed: %s", e)
        raise

    # Recover orphaned executions from previous crashes
    try:
        orphan_count = await recover_orphaned_executions()
        if orphan_count > 0:
            logger.warning(
                "Recovered %d orphaned execution(s) from previous session",
                orphan_count,
            )
    except Exception as e:
        logger.warning("Orphan recovery failed (non-fatal): %s", e)

    try:
        from app.execution.memory_lifecycle import MemoryLifecycleManager

        cleanup_stats = await MemoryLifecycleManager.rolling_cleanup()
        if any(v for v in cleanup_stats.values() if v):
            logger.info("Memory lifecycle cleanup: %s", cleanup_stats)
    except Exception as e:
        logger.warning("Memory lifecycle cleanup failed (non-fatal): %s", e)

    try:
        from app.execution.execution_stability import cleanup_orphan_executions_on_startup

        stale = await cleanup_orphan_executions_on_startup()
        if stale > 0:
            logger.warning("Cleaned up %d stale execution(s)", stale)
    except Exception as e:
        logger.warning("Stale execution cleanup failed (non-fatal): %s", e)

    try:
        from app.execution.runtime_hardening import RuntimeHardening

        maintenance = await RuntimeHardening.periodic_maintenance()
        if maintenance.get("deadWorkflowsCancelled"):
            logger.warning("Runtime hardening: %s", maintenance)
    except Exception as e:
        logger.warning("Runtime hardening maintenance failed (non-fatal): %s", e)

    try:
        from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

        init_result = await InfrastructureCoordinator.get_instance().initialize()
        logger.info("Infrastructure layer initialized: %s", init_result.get("profile"))
    except Exception as e:
        logger.warning("Infrastructure initialization failed (non-fatal): %s", e)

    # Optional startup benchmark suite (BENCHMARK_ON_STARTUP=1)
    try:
        from app.benchmark.benchmark_runner import run_benchmarks_on_startup
        import asyncio

        asyncio.create_task(run_benchmarks_on_startup())
    except Exception as e:
        logger.warning("Startup benchmark hook failed (non-fatal): %s", e)

    # Start orphan SSE queue sweep (automatic cleanup of abandoned queues)
    try:
        await event_manager.start_orphan_sweep()
        logger.info("Orphan SSE queue sweep started")
    except Exception as e:
        logger.warning("Failed to start orphan sweep (non-fatal): %s", e)

    yield  # Application runs here

    # ── Shutdown ───────────────────────────────────────────────
    logger.info("Shutting down...")

    # Cancel all running executions
    active_count = execution_manager.count_active()
    if active_count > 0:
        logger.warning("Cancelling %d running execution(s) on shutdown...", active_count)
        try:
            cancelled = await execution_manager.cancel_all(
                reason="Server shutting down"
            )
            logger.info("Cancelled %d execution(s) during shutdown", cancelled)
        except Exception as e:
            logger.error("Error cancelling executions on shutdown: %s", e)

    # Clean up all SSE event queues
    try:
        await event_manager.cleanup_all()
    except Exception as e:
        logger.error("Error cleaning up SSE queues: %s", e)

    # Clean up sandbox workspaces
    try:
        from app.execution.sandbox_manager import SandboxManager
        cleaned = SandboxManager.cleanup_all()
        if cleaned:
            logger.info("Cleaned up %d sandbox workspace(s)", cleaned)
    except Exception as e:
        logger.warning("Sandbox cleanup failed (non-fatal): %s", e)

    # Close database
    try:
        from app.infrastructure.infrastructure_coordinator import InfrastructureCoordinator

        await InfrastructureCoordinator.get_instance().shutdown()
    except Exception as e:
        logger.warning("Infrastructure shutdown failed (non-fatal): %s", e)

    try:
        await close_db()
    except Exception as e:
        logger.error("Error closing database: %s", e)

    logger.info("Shutdown complete")


# ── Application Factory ─────────────────────────────────────────
app = FastAPI(
    title="Personal AI Agent Workspace",
    version="0.1.0",
    lifespan=lifespan,
)


# ── CORS ────────────────────────────────────────────────────────
# Allow the Next.js frontend (localhost:3000) to call the API directly.
# In production, restrict origins to the actual frontend domain.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_url,
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Routes ──────────────────────────────────────────────────────
register_routes(app)

logger.info(
    "Backend ready: http://%s:%s",
    settings.host,
    settings.port,
)
logger.info("Health check: http://localhost:%s/api/health", settings.port)