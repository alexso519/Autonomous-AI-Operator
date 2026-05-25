"""
Route registration.

All route modules register their routers here.
Importing this module triggers FastAPI to include all endpoints.
"""

from fastapi import APIRouter

from app.routes.health import router as health_router
from app.routes.workflows import router as workflows_router
from app.routes.stream import router as stream_router
from app.routes.approvals import router as approvals_router
from app.routes.executions import router as executions_router
from app.routes.autonomous import router as autonomous_router

# Master router that collects all sub-routers
api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(workflows_router)
api_router.include_router(stream_router)
api_router.include_router(approvals_router)
api_router.include_router(executions_router)
api_router.include_router(autonomous_router)


def register_routes(app: "FastAPI") -> None:
    """Attach all routes to the FastAPI application."""
    app.include_router(api_router)