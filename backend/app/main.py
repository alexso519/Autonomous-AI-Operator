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

    # Close database
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