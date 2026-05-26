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
            id            TEXT PRIMARY KEY,
            workflow_id   TEXT NOT NULL,
            workflow_name TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'pending',
            started_at    TEXT,
            completed_at  TEXT,
            checkpoint    TEXT DEFAULT NULL,
            output        TEXT DEFAULT '{}',
            summary       TEXT DEFAULT '',
            shared_memory TEXT DEFAULT '{}',
            error         TEXT,
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
        # ── Tool Call Audit Trail ─────────────────────────────
        """CREATE TABLE IF NOT EXISTS tool_calls (
            id               TEXT PRIMARY KEY,
            execution_id     TEXT NOT NULL,
            node_id          TEXT NOT NULL,
            tool_name        TEXT NOT NULL,
            category         TEXT NOT NULL,
            permission_level TEXT NOT NULL,
            approval_required INTEGER NOT NULL DEFAULT 0,
            approved         INTEGER NOT NULL DEFAULT 0,
            status           TEXT NOT NULL DEFAULT 'pending',
            input            TEXT NOT NULL DEFAULT '{}',
            output           TEXT NOT NULL DEFAULT '{}',
            error            TEXT DEFAULT '',
            timestamp        TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Execution Summaries ──────────────────────────────
        """CREATE TABLE IF NOT EXISTS execution_state_transitions (
            id            TEXT PRIMARY KEY,
            execution_id  TEXT NOT NULL,
            timestamp     TEXT NOT NULL,
            from_state    TEXT NOT NULL,
            to_state      TEXT NOT NULL,
            reason        TEXT DEFAULT '',
            metadata      TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
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
        # ── Execution Quality Scores ─────────────────────────
        """CREATE TABLE IF NOT EXISTS execution_quality_scores (
            id                    TEXT PRIMARY KEY,
            execution_id          TEXT NOT NULL,
            node_id               TEXT NOT NULL,
            agent_name            TEXT NOT NULL DEFAULT '',
            overall_score         REAL NOT NULL DEFAULT 0,
            factuality_confidence REAL NOT NULL DEFAULT 0,
            completion_confidence REAL NOT NULL DEFAULT 0,
            repetition_score      REAL NOT NULL DEFAULT 0,
            hallucination_risk    REAL NOT NULL DEFAULT 0,
            tool_usage_quality    REAL NOT NULL DEFAULT 0,
            output_usefulness     REAL NOT NULL DEFAULT 0,
            reasons               TEXT NOT NULL DEFAULT '[]',
            issues                TEXT NOT NULL DEFAULT '[]',
            scored_at             TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Execution Checkpoint Snapshots ───────────────────
        """CREATE TABLE IF NOT EXISTS execution_checkpoints (
            id            TEXT PRIMARY KEY,
            execution_id  TEXT NOT NULL,
            snapshot_data TEXT NOT NULL DEFAULT '{}',
            created_at    TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Model routing history ─────────────────────────────
        """CREATE TABLE IF NOT EXISTS model_routing_history (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            node_id         TEXT DEFAULT '',
            agent_name      TEXT DEFAULT '',
            event_type      TEXT NOT NULL DEFAULT 'model_selected',
            model           TEXT NOT NULL,
            tier            TEXT NOT NULL,
            primary_model   TEXT NOT NULL,
            fallback_model  TEXT NOT NULL,
            reasons         TEXT NOT NULL DEFAULT '[]',
            context         TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Agent spawn plans ─────────────────────────────────
        """CREATE TABLE IF NOT EXISTS agent_spawn_plans (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            mode            TEXT NOT NULL,
            agent_count     INTEGER NOT NULL,
            task_type       TEXT NOT NULL,
            rationale       TEXT NOT NULL DEFAULT '[]',
            plan_data       TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Tool result cache ─────────────────────────────────
        """CREATE TABLE IF NOT EXISTS tool_result_cache (
            cache_key       TEXT PRIMARY KEY,
            tool_name       TEXT NOT NULL,
            input_hash      TEXT NOT NULL,
            output_data     TEXT NOT NULL DEFAULT '{}',
            execution_id    TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS tool_cache_events (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            tool_name       TEXT NOT NULL,
            cache_key       TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Memory archive ────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS execution_memory_archive (
            execution_id        TEXT PRIMARY KEY,
            snapshot_data       TEXT NOT NULL DEFAULT '{}',
            original_size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL
        )""",
        # ── Execution metrics & analytics ───────────────────
        """CREATE TABLE IF NOT EXISTS execution_metrics (
            id                  TEXT PRIMARY KEY,
            execution_id        TEXT NOT NULL,
            status              TEXT NOT NULL,
            duration_seconds    REAL NOT NULL DEFAULT 0,
            retry_count         INTEGER NOT NULL DEFAULT 0,
            tool_count          INTEGER NOT NULL DEFAULT 0,
            cache_hits          INTEGER NOT NULL DEFAULT 0,
            quality_avg         REAL,
            failure_reason      TEXT DEFAULT '',
            model_tier          TEXT DEFAULT '',
            created_at          TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id              TEXT PRIMARY KEY,
            snapshot_data   TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        # ── Benchmark runs ────────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS benchmark_runs (
            id                TEXT PRIMARY KEY,
            suite_id          TEXT DEFAULT '',
            task_id           TEXT NOT NULL,
            category          TEXT NOT NULL,
            execution_id      TEXT DEFAULT '',
            success           INTEGER NOT NULL DEFAULT 0,
            duration_seconds  REAL NOT NULL DEFAULT 0,
            metrics_data      TEXT NOT NULL DEFAULT '{}',
            created_at        TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS benchmark_suites (
            id              TEXT PRIMARY KEY,
            summary_data    TEXT NOT NULL DEFAULT '{}',
            results_count   INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        # ── Runtime performance snapshots ─────────────────────────
        """CREATE TABLE IF NOT EXISTS runtime_performance_snapshots (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            snapshot_data   TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Cognitive memory & self-improvement ─────────────────
        """CREATE TABLE IF NOT EXISTS cognitive_memories (
            id              TEXT PRIMARY KEY,
            memory_type     TEXT NOT NULL,
            execution_id    TEXT DEFAULT '',
            task_signature  TEXT NOT NULL DEFAULT '',
            content         TEXT NOT NULL DEFAULT '{}',
            snapshot_blob   TEXT DEFAULT '',
            retrieval_score REAL NOT NULL DEFAULT 0.5,
            use_count       INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS cognitive_optimizations (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            result_data     TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS self_improvement_cycles (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            cycle_data      TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        # ── Unified runtime state store ───────────────────────────
        """CREATE TABLE IF NOT EXISTS runtime_execution_snapshots (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            workflow_id     TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'running',
            snapshot_data   TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS runtime_action_snapshots (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            action_type     TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            node_id         TEXT DEFAULT '',
            payload         TEXT NOT NULL DEFAULT '{}',
            retry_lineage   TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS runtime_recovery_lineage (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            from_checkpoint TEXT NOT NULL DEFAULT '',
            to_action       TEXT NOT NULL DEFAULT '',
            reason          TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Persistent intelligence layer ───────────────────────
        """CREATE TABLE IF NOT EXISTS vector_memories (
            id              TEXT PRIMARY KEY,
            memory_type     TEXT NOT NULL,
            execution_id    TEXT DEFAULT '',
            source_id       TEXT DEFAULT '',
            content         TEXT NOT NULL,
            content_hash    TEXT NOT NULL DEFAULT '',
            embedding_blob  TEXT NOT NULL DEFAULT '[]',
            metadata        TEXT NOT NULL DEFAULT '{}',
            retrieval_score REAL NOT NULL DEFAULT 0.5,
            use_count       INTEGER NOT NULL DEFAULT 0,
            privacy_tag     TEXT DEFAULT 'internal',
            created_at      TEXT NOT NULL,
            expires_at      TEXT DEFAULT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS semantic_chunks (
            id              TEXT PRIMARY KEY,
            parent_id       TEXT NOT NULL,
            chunk_index     INTEGER NOT NULL DEFAULT 0,
            content         TEXT NOT NULL,
            embedding_blob  TEXT NOT NULL DEFAULT '[]',
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS embedding_cache (
            content_hash   TEXT PRIMARY KEY,
            model          TEXT NOT NULL,
            embedding_blob TEXT NOT NULL,
            token_count    INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS retrieval_history (
            id              TEXT PRIMARY KEY,
            query           TEXT NOT NULL,
            execution_id    TEXT DEFAULT '',
            results_count   INTEGER NOT NULL DEFAULT 0,
            top_score       REAL NOT NULL DEFAULT 0,
            memory_ids      TEXT NOT NULL DEFAULT '[]',
            context_tags    TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS entities (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            entity_type     TEXT NOT NULL DEFAULT 'concept',
            description     TEXT DEFAULT '',
            confidence      REAL NOT NULL DEFAULT 0.5,
            freshness_score REAL NOT NULL DEFAULT 1.0,
            metadata        TEXT NOT NULL DEFAULT '{}',
            first_seen_at   TEXT NOT NULL,
            last_seen_at    TEXT NOT NULL,
            mention_count   INTEGER NOT NULL DEFAULT 1
        )""",
        """CREATE TABLE IF NOT EXISTS entity_relationships (
            id              TEXT PRIMARY KEY,
            source_id       TEXT NOT NULL,
            target_id       TEXT NOT NULL,
            relation_type   TEXT NOT NULL DEFAULT 'related_to',
            confidence      REAL NOT NULL DEFAULT 0.5,
            evidence        TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS knowledge_facts (
            id              TEXT PRIMARY KEY,
            subject         TEXT NOT NULL,
            predicate       TEXT NOT NULL DEFAULT 'is',
            object_value    TEXT NOT NULL,
            confidence      REAL NOT NULL DEFAULT 0.5,
            freshness_score REAL NOT NULL DEFAULT 1.0,
            source_execution_id TEXT DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS fact_versions (
            id              TEXT PRIMARY KEY,
            fact_id         TEXT NOT NULL,
            version         INTEGER NOT NULL DEFAULT 1,
            object_value    TEXT NOT NULL,
            confidence      REAL NOT NULL DEFAULT 0.5,
            change_reason   TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS execution_entities (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            entity_id       TEXT NOT NULL,
            context_snippet TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS learned_strategies (
            id              TEXT PRIMARY KEY,
            strategy_label  TEXT NOT NULL,
            task_signature  TEXT NOT NULL DEFAULT '',
            approach        TEXT NOT NULL DEFAULT '',
            success_count   INTEGER NOT NULL DEFAULT 0,
            failure_count   INTEGER NOT NULL DEFAULT 0,
            avg_quality     REAL NOT NULL DEFAULT 0.5,
            avg_duration    REAL NOT NULL DEFAULT 0,
            composite_score REAL NOT NULL DEFAULT 0.5,
            metadata        TEXT NOT NULL DEFAULT '{}',
            last_used_at    TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS skill_profiles (
            id              TEXT PRIMARY KEY,
            profile_type    TEXT NOT NULL,
            profile_key     TEXT NOT NULL,
            success_count   INTEGER NOT NULL DEFAULT 0,
            failure_count   INTEGER NOT NULL DEFAULT 0,
            avg_quality     REAL NOT NULL DEFAULT 0.5,
            avg_duration    REAL NOT NULL DEFAULT 0,
            effectiveness   REAL NOT NULL DEFAULT 0.5,
            metadata        TEXT NOT NULL DEFAULT '{}',
            updated_at      TEXT NOT NULL,
            UNIQUE(profile_type, profile_key)
        )""",
        """CREATE TABLE IF NOT EXISTS execution_patterns (
            id              TEXT PRIMARY KEY,
            pattern_type    TEXT NOT NULL,
            task_signature  TEXT NOT NULL DEFAULT '',
            pattern_data    TEXT NOT NULL DEFAULT '{}',
            success_rate    REAL NOT NULL DEFAULT 0.5,
            use_count       INTEGER NOT NULL DEFAULT 0,
            source_execution_id TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS heuristic_evolution (
            id              TEXT PRIMARY KEY,
            heuristic_key   TEXT NOT NULL,
            old_value       REAL NOT NULL DEFAULT 0.5,
            new_value       REAL NOT NULL DEFAULT 0.5,
            change_reason   TEXT DEFAULT '',
            source_execution_id TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        # ── Computer Use / Visual Perception ──────────────────────
        """CREATE TABLE IF NOT EXISTS screen_snapshots (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            image_path      TEXT NOT NULL DEFAULT '',
            width           INTEGER NOT NULL DEFAULT 0,
            height          INTEGER NOT NULL DEFAULT 0,
            fingerprint     TEXT NOT NULL DEFAULT '',
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS visual_elements (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            snapshot_id     TEXT NOT NULL,
            element_type    TEXT NOT NULL DEFAULT 'unknown',
            label           TEXT NOT NULL DEFAULT '',
            bbox_json       TEXT NOT NULL DEFAULT '[]',
            confidence      REAL NOT NULL DEFAULT 0,
            interactive     INTEGER NOT NULL DEFAULT 0,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS ocr_extractions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            snapshot_id     TEXT NOT NULL,
            engine          TEXT NOT NULL DEFAULT 'heuristic',
            text_content    TEXT NOT NULL DEFAULT '',
            regions_json    TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS ui_state_history (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            snapshot_id     TEXT NOT NULL DEFAULT '',
            state_hash      TEXT NOT NULL DEFAULT '',
            change_detected INTEGER NOT NULL DEFAULT 0,
            state_data      TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS ui_navigation_paths (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            path_nodes      TEXT NOT NULL DEFAULT '[]',
            path_data       TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS planned_actions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            action_type     TEXT NOT NULL,
            target          TEXT NOT NULL DEFAULT '',
            coordinates_json TEXT NOT NULL DEFAULT '[]',
            confidence      REAL NOT NULL DEFAULT 0,
            plan_data       TEXT NOT NULL DEFAULT '{}',
            status          TEXT NOT NULL DEFAULT 'planned',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS executed_actions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            planned_action_id TEXT NOT NULL DEFAULT '',
            success         INTEGER NOT NULL DEFAULT 0,
            result_data     TEXT NOT NULL DEFAULT '{}',
            error           TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS failed_ui_actions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            action_type     TEXT NOT NULL,
            reason          TEXT NOT NULL DEFAULT '',
            recovery_attempted INTEGER NOT NULL DEFAULT 0,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Persistent Desktop Sessions (Phase 2) ─────────────────
        """CREATE TABLE IF NOT EXISTS desktop_sessions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            session_id      TEXT NOT NULL DEFAULT '',
            mode            TEXT NOT NULL DEFAULT 'desktop',
            status          TEXT NOT NULL DEFAULT 'active',
            active_app      TEXT NOT NULL DEFAULT '',
            window_state    TEXT NOT NULL DEFAULT '{}',
            session_data    TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS workspace_snapshots (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            session_id      TEXT NOT NULL DEFAULT '',
            label           TEXT NOT NULL DEFAULT '',
            snapshot_data   TEXT NOT NULL DEFAULT '{}',
            image_path      TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS application_states (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            session_id      TEXT NOT NULL DEFAULT '',
            app_name        TEXT NOT NULL DEFAULT '',
            window_title    TEXT NOT NULL DEFAULT '',
            focus_state     TEXT NOT NULL DEFAULT 'background',
            state_data      TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS desktop_checkpoints (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            session_id      TEXT NOT NULL DEFAULT '',
            checkpoint_label TEXT NOT NULL DEFAULT '',
            chain_id        TEXT NOT NULL DEFAULT '',
            step_index      INTEGER NOT NULL DEFAULT 0,
            checkpoint_data TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        # ── Distributed runtime infrastructure ──────────────────────
        """CREATE TABLE IF NOT EXISTS distributed_jobs (
            id                      TEXT PRIMARY KEY,
            execution_id            TEXT NOT NULL,
            workflow_id             TEXT NOT NULL DEFAULT '',
            payload                 TEXT NOT NULL DEFAULT '{}',
            priority                INTEGER NOT NULL DEFAULT 5,
            status                  TEXT NOT NULL DEFAULT 'pending',
            required_capabilities   TEXT NOT NULL DEFAULT '[]',
            worker_id               TEXT NOT NULL DEFAULT '',
            retry_count             INTEGER NOT NULL DEFAULT 0,
            max_retries             INTEGER NOT NULL DEFAULT 3,
            available_at            TEXT NOT NULL,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS worker_nodes (
            id                  TEXT PRIMARY KEY,
            hostname            TEXT NOT NULL DEFAULT 'local',
            capabilities        TEXT NOT NULL DEFAULT '[]',
            max_concurrency     INTEGER NOT NULL DEFAULT 4,
            active_jobs         INTEGER NOT NULL DEFAULT 0,
            status              TEXT NOT NULL DEFAULT 'healthy',
            last_heartbeat      TEXT NOT NULL,
            metadata            TEXT NOT NULL DEFAULT '{}',
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS job_leases (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL,
            worker_id       TEXT NOT NULL,
            execution_id    TEXT NOT NULL,
            acquired_at     TEXT NOT NULL,
            renewed_at      TEXT NOT NULL,
            expires_at      TEXT NOT NULL,
            released_at     TEXT,
            FOREIGN KEY (job_id) REFERENCES distributed_jobs(id)
        )""",
        """CREATE TABLE IF NOT EXISTS job_retry_history (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL,
            execution_id    TEXT NOT NULL,
            retry_number    INTEGER NOT NULL DEFAULT 1,
            error           TEXT NOT NULL DEFAULT '',
            scheduled_at    TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES distributed_jobs(id)
        )""",
        # ── Persistent execution infrastructure ───────────────────
        """CREATE TABLE IF NOT EXISTS persistent_executions (
            id              TEXT PRIMARY KEY,
            workflow_id     TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'running',
            checkpoint_data TEXT NOT NULL DEFAULT '{}',
            resume_token    TEXT NOT NULL DEFAULT '',
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS execution_snapshots (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            label           TEXT NOT NULL DEFAULT '',
            snapshot_data   TEXT NOT NULL DEFAULT '{}',
            action_count    INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS execution_lineage (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            snapshot_data   TEXT NOT NULL DEFAULT '{}',
            lineage_data    TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS recovery_events (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            event_type      TEXT NOT NULL,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        # ── Enterprise auth + multi-tenant ────────────────────────
        """CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            email       TEXT NOT NULL DEFAULT '',
            roles       TEXT NOT NULL DEFAULT '["viewer"]',
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            created_at  TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS api_keys (
            id              TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            key_hash        TEXT NOT NULL,
            label           TEXT NOT NULL DEFAULT '',
            expires_at      TEXT,
            last_used_at    TEXT,
            revoked_at      TEXT,
            created_at      TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        """CREATE TABLE IF NOT EXISTS tenant_configs (
            id                          TEXT PRIMARY KEY,
            name                        TEXT NOT NULL DEFAULT '',
            max_concurrent_executions   INTEGER NOT NULL DEFAULT 10,
            max_queue_depth             INTEGER NOT NULL DEFAULT 100,
            namespace                   TEXT NOT NULL DEFAULT 'default',
            quotas                      TEXT NOT NULL DEFAULT '{}',
            created_at                  TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS tenant_usage (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            execution_id    TEXT NOT NULL DEFAULT '',
            action          TEXT NOT NULL DEFAULT '',
            units           REAL NOT NULL DEFAULT 1.0,
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS audit_logs (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL DEFAULT '',
            tenant_id   TEXT NOT NULL DEFAULT 'default',
            action      TEXT NOT NULL,
            resource    TEXT NOT NULL DEFAULT '',
            metadata    TEXT NOT NULL DEFAULT '{}',
            ip_address  TEXT NOT NULL DEFAULT '',
            created_at  TEXT NOT NULL
        )""",
        # ── Federated runtime mesh ────────────────────────────────
        """CREATE TABLE IF NOT EXISTS runtime_clusters (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL DEFAULT '',
            topology        TEXT NOT NULL DEFAULT '{}',
            health_score    REAL NOT NULL DEFAULT 1.0,
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS cluster_nodes (
            id              TEXT PRIMARY KEY,
            cluster_id      TEXT NOT NULL,
            node_id         TEXT NOT NULL,
            hostname        TEXT NOT NULL DEFAULT 'local',
            capabilities    TEXT NOT NULL DEFAULT '[]',
            affinity_tags   TEXT NOT NULL DEFAULT '[]',
            health_score    REAL NOT NULL DEFAULT 1.0,
            status          TEXT NOT NULL DEFAULT 'healthy',
            last_heartbeat  TEXT NOT NULL,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (cluster_id) REFERENCES runtime_clusters(id)
        )""",
        """CREATE TABLE IF NOT EXISTS remote_executions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            source_node     TEXT NOT NULL DEFAULT 'local',
            target_node     TEXT NOT NULL DEFAULT '',
            shard_key       TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending',
            replay_data     TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL,
            completed_at    TEXT,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS memory_sync_events (
            id              TEXT PRIMARY KEY,
            cluster_id      TEXT NOT NULL DEFAULT '',
            source_node     TEXT NOT NULL DEFAULT 'local',
            target_node     TEXT NOT NULL DEFAULT '',
            sync_type       TEXT NOT NULL DEFAULT 'incremental',
            payload_size    INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'completed',
            created_at      TEXT NOT NULL
        )""",
        # ── Enterprise governance ─────────────────────────────────
        """CREATE TABLE IF NOT EXISTS runtime_policies (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            category        TEXT NOT NULL DEFAULT 'security',
            rules           TEXT NOT NULL DEFAULT '{}',
            enabled         INTEGER NOT NULL DEFAULT 1,
            simulation_mode INTEGER NOT NULL DEFAULT 0,
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS policy_violations (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL DEFAULT '',
            policy_id       TEXT NOT NULL DEFAULT '',
            category        TEXT NOT NULL DEFAULT '',
            severity        TEXT NOT NULL DEFAULT 'medium',
            message         TEXT NOT NULL DEFAULT '',
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS approval_requests (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            action_type     TEXT NOT NULL DEFAULT '',
            capability      TEXT NOT NULL DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending',
            requested_by    TEXT NOT NULL DEFAULT 'system',
            decided_by      TEXT DEFAULT '',
            notes           TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL,
            decided_at      TEXT,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS compliance_records (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL DEFAULT '',
            profile         TEXT NOT NULL DEFAULT 'local_dev',
            classification  TEXT NOT NULL DEFAULT 'internal',
            check_result    TEXT NOT NULL DEFAULT '{}',
            passed          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL
        )""",
        # ── Financial metering ────────────────────────────────────
        """CREATE TABLE IF NOT EXISTS runtime_costs (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL DEFAULT '',
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            cost_type       TEXT NOT NULL DEFAULT 'model',
            units           REAL NOT NULL DEFAULT 0,
            estimated_cost  REAL NOT NULL DEFAULT 0,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS usage_budgets (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            budget_limit    REAL NOT NULL DEFAULT 100.0,
            spent           REAL NOT NULL DEFAULT 0,
            period          TEXT NOT NULL DEFAULT 'monthly',
            status          TEXT NOT NULL DEFAULT 'active',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS execution_usage (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL,
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            model_tokens    INTEGER NOT NULL DEFAULT 0,
            tool_calls      INTEGER NOT NULL DEFAULT 0,
            browser_actions INTEGER NOT NULL DEFAULT 0,
            desktop_actions INTEGER NOT NULL DEFAULT 0,
            worker_cost     REAL NOT NULL DEFAULT 0,
            storage_bytes   INTEGER NOT NULL DEFAULT 0,
            total_cost      REAL NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (execution_id) REFERENCES executions(id)
        )""",
        """CREATE TABLE IF NOT EXISTS tenant_billing (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL DEFAULT 'default',
            period          TEXT NOT NULL DEFAULT '',
            total_cost      REAL NOT NULL DEFAULT 0,
            execution_count INTEGER NOT NULL DEFAULT 0,
            metadata        TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        # ── Bounded self-improvement layer ────────────────────────
        """CREATE TABLE IF NOT EXISTS heuristic_generations (
            id              TEXT PRIMARY KEY,
            generation      INTEGER NOT NULL DEFAULT 1,
            heuristic_data  TEXT NOT NULL DEFAULT '{}',
            score           REAL NOT NULL DEFAULT 0.5,
            source_execution_id TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS strategy_mutations (
            id              TEXT PRIMARY KEY,
            parent_id       TEXT DEFAULT '',
            mutation_type   TEXT NOT NULL DEFAULT 'weight_adjust',
            mutation_data   TEXT NOT NULL DEFAULT '{}',
            fitness_score   REAL NOT NULL DEFAULT 0.5,
            lineage         TEXT NOT NULL DEFAULT '[]',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS runtime_optimizations (
            id              TEXT PRIMARY KEY,
            optimization_type TEXT NOT NULL DEFAULT 'bottleneck',
            target          TEXT NOT NULL DEFAULT '',
            before_value    TEXT NOT NULL DEFAULT '{}',
            after_value     TEXT NOT NULL DEFAULT '{}',
            confidence      REAL NOT NULL DEFAULT 0.5,
            applied         INTEGER NOT NULL DEFAULT 0,
            source_execution_id TEXT DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS improvement_memory (
            id              TEXT PRIMARY KEY,
            memory_key      TEXT NOT NULL,
            memory_type     TEXT NOT NULL DEFAULT 'pattern',
            memory_data     TEXT NOT NULL DEFAULT '{}',
            confidence      REAL NOT NULL DEFAULT 0.5,
            use_count       INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS failure_clusters (
            id              TEXT PRIMARY KEY,
            cluster_key     TEXT NOT NULL,
            failure_type    TEXT NOT NULL DEFAULT '',
            pattern_data    TEXT NOT NULL DEFAULT '{}',
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            suppressed      INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS prompt_variants (
            id              TEXT PRIMARY KEY,
            role            TEXT NOT NULL DEFAULT 'agent',
            template_key    TEXT NOT NULL DEFAULT '',
            template_body   TEXT NOT NULL DEFAULT '',
            effectiveness   REAL NOT NULL DEFAULT 0.5,
            token_efficiency REAL NOT NULL DEFAULT 0.5,
            metadata        TEXT NOT NULL DEFAULT '{}',
            active          INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS planning_profiles (
            id              TEXT PRIMARY KEY,
            profile_key     TEXT NOT NULL,
            depth           INTEGER NOT NULL DEFAULT 3,
            reflection_threshold REAL NOT NULL DEFAULT 0.5,
            context_allocation REAL NOT NULL DEFAULT 0.5,
            profile_data    TEXT NOT NULL DEFAULT '{}',
            effectiveness   REAL NOT NULL DEFAULT 0.5,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS retry_profiles (
            id              TEXT PRIMARY KEY,
            profile_key     TEXT NOT NULL,
            profile_data    TEXT NOT NULL DEFAULT '{}',
            max_retries     INTEGER NOT NULL DEFAULT 3,
            backoff_factor  REAL NOT NULL DEFAULT 1.5,
            effectiveness   REAL NOT NULL DEFAULT 0.5,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS tool_selection_profiles (
            id              TEXT PRIMARY KEY,
            profile_key     TEXT NOT NULL,
            preferred_tools TEXT NOT NULL DEFAULT '[]',
            avoided_tools   TEXT NOT NULL DEFAULT '[]',
            profile_data    TEXT NOT NULL DEFAULT '{}',
            effectiveness   REAL NOT NULL DEFAULT 0.5,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS benchmark_evolution (
            id              TEXT PRIMARY KEY,
            suite_id        TEXT DEFAULT '',
            evolution_data  TEXT NOT NULL DEFAULT '{}',
            trend_score     REAL NOT NULL DEFAULT 0.5,
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS regression_events (
            id              TEXT PRIMARY KEY,
            metric          TEXT NOT NULL DEFAULT 'success_rate',
            before_value    REAL NOT NULL DEFAULT 0,
            after_value     REAL NOT NULL DEFAULT 0,
            severity        TEXT NOT NULL DEFAULT 'warning',
            event_data      TEXT NOT NULL DEFAULT '{}',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS capability_trends (
            id              TEXT PRIMARY KEY,
            capability      TEXT NOT NULL,
            trend_data      TEXT NOT NULL DEFAULT '{}',
            score           REAL NOT NULL DEFAULT 0.5,
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS performance_evolution (
            id              TEXT PRIMARY KEY,
            metric          TEXT NOT NULL DEFAULT 'latency',
            evolution_data  TEXT NOT NULL DEFAULT '{}',
            score           REAL NOT NULL DEFAULT 0.5,
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS improvement_sessions (
            id              TEXT PRIMARY KEY,
            execution_id    TEXT NOT NULL DEFAULT '',
            session_data    TEXT NOT NULL DEFAULT '{}',
            status          TEXT NOT NULL DEFAULT 'completed',
            dry_run         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            completed_at    TEXT DEFAULT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS evolution_policies (
            id              TEXT PRIMARY KEY,
            policy_data     TEXT NOT NULL DEFAULT '{}',
            reason          TEXT DEFAULT '',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS optimization_rollbacks (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL DEFAULT '',
            rollback_data   TEXT NOT NULL DEFAULT '{}',
            trigger_reason  TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS improvement_audits (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL DEFAULT '',
            action_type     TEXT NOT NULL DEFAULT '',
            audit_data      TEXT NOT NULL DEFAULT '{}',
            replayable      INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL
        )""",
    ]

    # Create all tables with error handling
    for table_sql in tables:
        try:
            await conn.execute(table_sql)
            logger.debug("Table created or already exists")
        except Exception as e:
            logger.warning("Error creating table (continuing): %s", e)

    # ── Migrate executions table to add checkpoint column and indexes ──
    # This is safe to run multiple times — IF NOT EXISTS for column add
    for migration_sql in [
        """ALTER TABLE executions ADD COLUMN checkpoint TEXT DEFAULT NULL""",
        """ALTER TABLE executions ADD COLUMN workflow_name TEXT DEFAULT ''""",
        """ALTER TABLE executions ADD COLUMN summary TEXT DEFAULT ''""",
        """ALTER TABLE executions ADD COLUMN shared_memory TEXT DEFAULT '{}'""",
        """CREATE INDEX IF NOT EXISTS idx_event_logs_execution_id
           ON execution_event_logs(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_executions_workflow_id
           ON executions(workflow_id)""",
        """CREATE INDEX IF NOT EXISTS idx_tool_calls_execution_id
           ON tool_calls(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_execution_state_transitions_execution_id
           ON execution_state_transitions(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_execution_quality_scores_execution_id
           ON execution_quality_scores(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_execution_checkpoints_execution_id
           ON execution_checkpoints(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_model_routing_execution_id
           ON model_routing_history(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_tool_cache_tool_name
           ON tool_result_cache(tool_name)""",
        """CREATE INDEX IF NOT EXISTS idx_execution_metrics_execution_id
           ON execution_metrics(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_benchmark_runs_created_at
           ON benchmark_runs(created_at)""",
        """CREATE INDEX IF NOT EXISTS idx_runtime_performance_execution_id
           ON runtime_performance_snapshots(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_cognitive_memories_signature
           ON cognitive_memories(task_signature)""",
        """CREATE INDEX IF NOT EXISTS idx_cognitive_memories_type
           ON cognitive_memories(memory_type)""",
        """CREATE INDEX IF NOT EXISTS idx_runtime_execution_snapshots_execution_id
           ON runtime_execution_snapshots(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_runtime_action_snapshots_execution_id
           ON runtime_action_snapshots(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_runtime_recovery_lineage_execution_id
           ON runtime_recovery_lineage(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_vector_memories_type
           ON vector_memories(memory_type)""",
        """CREATE INDEX IF NOT EXISTS idx_vector_memories_hash
           ON vector_memories(content_hash)""",
        """CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)""",
        """CREATE INDEX IF NOT EXISTS idx_screen_snapshots_execution_id
           ON screen_snapshots(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_visual_elements_execution_id
           ON visual_elements(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_planned_actions_execution_id
           ON planned_actions(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_executed_actions_execution_id
           ON executed_actions(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_failed_ui_actions_execution_id
           ON failed_ui_actions(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_learned_strategies_signature
           ON learned_strategies(task_signature)""",
        """CREATE INDEX IF NOT EXISTS idx_knowledge_facts_subject
           ON knowledge_facts(subject)""",
        """CREATE INDEX IF NOT EXISTS idx_distributed_jobs_status
           ON distributed_jobs(status)""",
        """CREATE INDEX IF NOT EXISTS idx_distributed_jobs_execution_id
           ON distributed_jobs(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_worker_nodes_status
           ON worker_nodes(status)""",
        """CREATE INDEX IF NOT EXISTS idx_job_leases_job_id
           ON job_leases(job_id)""",
        """CREATE INDEX IF NOT EXISTS idx_persistent_executions_status
           ON persistent_executions(status)""",
        """CREATE INDEX IF NOT EXISTS idx_execution_snapshots_execution_id
           ON execution_snapshots(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_recovery_events_execution_id
           ON recovery_events(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_audit_logs_tenant_id
           ON audit_logs(tenant_id)""",
        """CREATE INDEX IF NOT EXISTS idx_cluster_nodes_cluster_id
           ON cluster_nodes(cluster_id)""",
        """CREATE INDEX IF NOT EXISTS idx_remote_executions_execution_id
           ON remote_executions(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_policy_violations_execution_id
           ON policy_violations(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_approval_requests_execution_id
           ON approval_requests(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_runtime_costs_execution_id
           ON runtime_costs(execution_id)""",
        """CREATE INDEX IF NOT EXISTS idx_execution_usage_execution_id
           ON execution_usage(execution_id)""",
    ]:
        try:
            await conn.execute(migration_sql)
        except Exception as e:
            logger.debug("Migration skipped (likely already exists): %s", e)

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