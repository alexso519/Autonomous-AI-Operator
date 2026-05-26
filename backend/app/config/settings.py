"""
Application settings loaded from environment variables.

Kept intentionally simple — no pydantic-settings, no .env file loading.
In Docker Compose, all values are set in the environment block.
For local development, set them in your shell or use docker compose.
"""

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # ── Server ──────────────────────────────────────────────
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(
        default_factory=lambda: int(os.getenv("BACKEND_PORT", "8000"))
    )

    # ── Ollama ──────────────────────────────────────────────
    ollama_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    )
    ollama_model_light: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL_LIGHT", "")
    )
    ollama_model_strong: str = field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL_STRONG", "")
    )

    # ── Persistent intelligence layer ────────────────────────
    enable_vector_memory: bool = field(
        default_factory=lambda: os.getenv("ENABLE_VECTOR_MEMORY", "1") == "1"
    )
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    )
    max_long_term_memories: int = field(
        default_factory=lambda: int(os.getenv("MAX_LONG_TERM_MEMORIES", "5000"))
    )
    world_model_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("WORLD_MODEL_CONFIDENCE_THRESHOLD", "0.3"))
    )

    # ── Production readiness ─────────────────────────────────
    memory_retention_days: int = field(
        default_factory=lambda: int(os.getenv("MEMORY_RETENTION_DAYS", "30"))
    )
    memory_max_shared_kb: int = field(
        default_factory=lambda: int(os.getenv("MEMORY_MAX_SHARED_KB", "256"))
    )
    max_execution_duration_seconds: int = field(
        default_factory=lambda: int(os.getenv("MAX_EXECUTION_DURATION_SECONDS", "3600"))
    )

    # ── Sandbox / Computer Use ──────────────────────────────
    sandbox_max_bytes: int = field(
        default_factory=lambda: int(os.getenv("SANDBOX_MAX_BYTES", str(50 * 1024 * 1024)))
    )
    browser_headless: bool = field(
        default_factory=lambda: os.getenv("BROWSER_HEADLESS", "1") == "1"
    )
    terminal_default_timeout: int = field(
        default_factory=lambda: int(os.getenv("TERMINAL_DEFAULT_TIMEOUT", "60"))
    )

    # ── Computer Use / Multimodal Desktop ───────────────────
    enable_computer_use: bool = field(
        default_factory=lambda: os.getenv("ENABLE_COMPUTER_USE", "1") == "1"
    )
    computer_use_headless: bool = field(
        default_factory=lambda: os.getenv("COMPUTER_USE_HEADLESS", "1") == "1"
    )
    ocr_engine: str = field(
        default_factory=lambda: os.getenv("OCR_ENGINE", "tesseract")
    )
    max_screenshot_history: int = field(
        default_factory=lambda: int(os.getenv("MAX_SCREENSHOT_HISTORY", "50"))
    )
    ui_action_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("UI_ACTION_TIMEOUT_SECONDS", "120"))
    )
    computer_use_max_actions_per_minute: int = field(
        default_factory=lambda: int(os.getenv("COMPUTER_USE_MAX_ACTIONS_PER_MINUTE", "60"))
    )

    # ── Database ────────────────────────────────────────────
    database_path: str = field(
        default_factory=lambda: os.getenv("DATABASE_PATH", "./data/workflows.db")
    )

    # ── CORS ────────────────────────────────────────────────
    frontend_url: str = field(
        default_factory=lambda: os.getenv("FRONTEND_URL", "http://localhost:3000")
    )

    # ── Logging ─────────────────────────────────────────────
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )

    # ── Distributed runtime / production infrastructure ───────
    enable_distributed_runtime: bool = field(
        default_factory=lambda: os.getenv("ENABLE_DISTRIBUTED_RUNTIME", "0") == "1"
    )
    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "")
    )
    postgres_url: str = field(
        default_factory=lambda: os.getenv("POSTGRES_URL", "")
    )
    enable_rbac: bool = field(
        default_factory=lambda: os.getenv("ENABLE_RBAC", "0") == "1"
    )
    enable_multi_tenancy: bool = field(
        default_factory=lambda: os.getenv("ENABLE_MULTI_TENANCY", "0") == "1"
    )
    enable_otel_tracing: bool = field(
        default_factory=lambda: os.getenv("ENABLE_OTEL_TRACING", "0") == "1"
    )
    max_worker_concurrency: int = field(
        default_factory=lambda: int(os.getenv("MAX_WORKER_CONCURRENCY", "4"))
    )
    worker_heartbeat_seconds: int = field(
        default_factory=lambda: int(os.getenv("WORKER_HEARTBEAT_SECONDS", "30"))
    )
    jwt_secret: str = field(
        default_factory=lambda: os.getenv("JWT_SECRET", "")
    )

    # ── Enterprise infrastructure phase 2 ─────────────────────
    enable_runtime_mesh: bool = field(
        default_factory=lambda: os.getenv("ENABLE_RUNTIME_MESH", "0") == "1"
    )
    enable_policy_engine: bool = field(
        default_factory=lambda: os.getenv("ENABLE_POLICY_ENGINE", "0") == "1"
    )
    enable_cost_metering: bool = field(
        default_factory=lambda: os.getenv("ENABLE_COST_METERING", "0") == "1"
    )
    enable_infra_intelligence: bool = field(
        default_factory=lambda: os.getenv("ENABLE_INFRA_INTELLIGENCE", "0") == "1"
    )
    enable_autoscaling: bool = field(
        default_factory=lambda: os.getenv("ENABLE_AUTOSCALING", "0") == "1"
    )
    enable_approval_workflows: bool = field(
        default_factory=lambda: os.getenv("ENABLE_APPROVAL_WORKFLOWS", "0") == "1"
    )
    compliance_mode: str = field(
        default_factory=lambda: os.getenv("COMPLIANCE_MODE", "local_dev")
    )
    default_execution_budget: float = field(
        default_factory=lambda: float(os.getenv("DEFAULT_EXECUTION_BUDGET", "100.0"))
    )
    runtime_mesh_node_id: str = field(
        default_factory=lambda: os.getenv("RUNTIME_MESH_NODE_ID", "local")
    )

    # ── Bounded self-improvement (optional, off by default) ───
    enable_self_improvement: bool = field(
        default_factory=lambda: os.getenv("ENABLE_SELF_IMPROVEMENT", "0") == "1"
    )
    enable_prompt_evolution: bool = field(
        default_factory=lambda: os.getenv("ENABLE_PROMPT_EVOLUTION", "0") == "1"
    )
    enable_benchmark_optimization: bool = field(
        default_factory=lambda: os.getenv("ENABLE_BENCHMARK_OPTIMIZATION", "0") == "1"
    )
    enable_runtime_adaptation: bool = field(
        default_factory=lambda: os.getenv("ENABLE_RUNTIME_ADAPTATION", "0") == "1"
    )
    max_heuristic_mutations_per_day: int = field(
        default_factory=lambda: int(os.getenv("MAX_HEURISTIC_MUTATIONS_PER_DAY", "50"))
    )
    max_prompt_variants: int = field(
        default_factory=lambda: int(os.getenv("MAX_PROMPT_VARIANTS", "20"))
    )
    self_improvement_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("SELF_IMPROVEMENT_CONFIDENCE_THRESHOLD", "0.75"))
    )
    self_improvement_dry_run: bool = field(
        default_factory=lambda: os.getenv("SELF_IMPROVEMENT_DRY_RUN", "1") == "1"
    )


# Singleton — import this anywhere to get current settings.
settings = Settings()