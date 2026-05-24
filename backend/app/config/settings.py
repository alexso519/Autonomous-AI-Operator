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


# Singleton — import this anywhere to get current settings.
settings = Settings()