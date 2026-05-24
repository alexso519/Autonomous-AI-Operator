"""
Health check endpoint.

Returns the service status. Used by:
- Frontend to verify backend connectivity
- Docker health checks (future)
- Quick debugging
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from app.config.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health_check():
    """Return the current service status."""
    return {
        "status": "ok",
        "service": "personal-ai-workspace",
        "version": "0.1.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database_path": settings.database_path,
        "ollama_url": settings.ollama_url,
        "ollama_model": settings.ollama_model,
    }