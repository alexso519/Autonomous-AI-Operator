"""
SQLite database initialization.

Keeps it simple:
- Auto-creates the database file and directory if they don't exist.
- Creates tables on first run.
- Uses aiosqlite for async access.
- No ORM — raw SQL with parameterized queries.
"""

import os
import aiosqlite
import logging

from app.config.settings import settings

logger = logging.getLogger(__name__)

# Module-level connection reference.
# In production we'd use a connection pool, but for a single-user PoC
# this is sufficient. Each request gets its own cursor.
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the shared database connection, creating it if needed."""
    global _db
    if _db is None:
        _db = await _create_connection()
    return _db


async def _create_connection() -> aiosqlite.Connection:
    """Create the database directory, file, and tables."""
    db_path = settings.database_path
    db_dir = os.path.dirname(db_path)

    # Ensure the directory exists
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.info("Created database directory: %s", db_dir)

    # Open connection — WAL mode for better concurrent read performance
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    
    # Try to enable WAL mode, but gracefully fall back if it fails (e.g., in Docker with volume issues)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        logger.info("Database WAL mode enabled")
    except Exception as e:
        logger.warning("Failed to enable WAL mode (continuing with default journal): %s", e)
    
    # Try to enable foreign keys, but log if it fails
    try:
        await conn.execute("PRAGMA foreign_keys=ON")
    except Exception as e:
        logger.warning("Failed to enable foreign keys pragma: %s", e)

    logger.info("Database connection opened: %s", db_path)

    # Create tables
    await _init_tables(conn)

    return conn


async def _init_tables(conn: aiosqlite.Connection) -> None:
    """Create tables if they don't exist."""
    tables = [
        # ── Workflows ──────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS workflows (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            nodes       TEXT NOT NULL DEFAULT '[]',
            edges       TEXT NOT NULL DEFAULT '[]',
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        # ── Execution Logs ─────────────────────────────────
        """CREATE TABLE IF NOT EXISTS executions (
            id          TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            started_at  TEXT,
            completed_at TEXT,
            output      TEXT DEFAULT '{}',
            error       TEXT,
            FOREIGN KEY (workflow_id) REFERENCES workflows(id)
        )""",
        # ── Execution Steps (per-node logs) ────────────────
        """CREATE TABLE IF NOT EXISTS execution_steps (
            id            TEXT PRIMARY KEY,
            execution_id  TEXT NOT NULL,
            node_id       TEXT NOT NULL,
            agent_name    TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'pending',
            input         TEXT DEFAULT '',
            output        TEXT DEFAULT '',
            started_at    TEXT,
            completed_at  TEXT,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Execution Approvals ─────────────────────────────
        """CREATE TABLE IF NOT EXISTS execution_approvals (
            id            TEXT PRIMARY KEY,
            execution_id  TEXT NOT NULL,
            node_id       TEXT NOT NULL,
            agent_name    TEXT NOT NULL DEFAULT 'Approval Gate',
            status        TEXT NOT NULL DEFAULT 'pending',
            requested_at  TEXT NOT NULL,
            decided_at    TEXT,
            decided_by    TEXT DEFAULT 'user',
            notes         TEXT DEFAULT '',
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Execution Event Logs (persistent event store) ────
        """CREATE TABLE IF NOT EXISTS execution_event_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            execution_id  TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            timestamp     TEXT NOT NULL,
            agent         TEXT NOT NULL DEFAULT 'system',
            message       TEXT NOT NULL DEFAULT '',
            data          TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Execution Summaries ──────────────────────────────
        """CREATE TABLE IF NOT EXISTS execution_summaries (
            id            TEXT PRIMARY KEY,
            execution_id  TEXT NOT NULL UNIQUE,
            workflow_id   TEXT NOT NULL,
            workflow_name TEXT NOT NULL DEFAULT '',
            total_steps   INTEGER NOT NULL DEFAULT 0,
            completed_steps INTEGER NOT NULL DEFAULT 0,
            failed_steps  INTEGER NOT NULL DEFAULT 0,
            total_duration_seconds REAL NOT NULL DEFAULT 0,
            output_summary TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (execution_id) REFERENCES executions(id),
            FOREIGN KEY (workflow_id) REFERENCES workflows(id)
        )""",
    ]

    # ── Migrate executions table to add checkpoint column ──
    # This is safe to run multiple times — IF NOT EXISTS for column add
    for migration_sql in [
        """ALTER TABLE executions ADD COLUMN checkpoint TEXT DEFAULT NULL""",
        """ALTER TABLE executions ADD COLUMN workflow_name TEXT DEFAULT ''""",
        """ALTER TABLE executions ADD COLUMN summary TEXT DEFAULT ''""",
        """CREATE INDEX IF NOT EXISTS idx_event_logs_execution_id
           ON execution_event_logs(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_executions_workflow_id
           ON executions(workflow_id)""",
    ]:
        try:
            await conn.execute(migration_sql)
        except Exception as e:
            logger.debug("Migration skipped (likely already exists): %s", e)

    # Create all tables with error handling
    for table_sql in tables:
        try:
            await conn.execute(table_sql)
            logger.debug("Table created or already exists")
        except Exception as e:
            logger.warning("Error creating table (continuing): %s", e)

    try:
        await conn.commit()
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.warning("Error committing table creation: %s", e)


async def close_db() -> None:
    """Close the database connection during shutdown."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        logger.info("Database connection closed")