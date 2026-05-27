from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.execution.model_preferences import get_model_preference_store

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelConfigResponse(BaseModel):
    provider: str
    model: str
    temperature: float
    maxTokens: int
    hasApiKey: bool
    apiKeyMasked: str | None = None


class UpdateModelConfigRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    maxTokens: int | None = Field(default=None, ge=128, le=65536)
    apiKey: str | None = None


@router.get("/options")
async def get_model_options() -> dict[str, list[str]]:
    store = get_model_preference_store()
    return store.get_options()


@router.get("/config")
async def get_model_config() -> ModelConfigResponse:
    current = get_model_preference_store().get()
    masked = None
    if current.api_key:
        key = current.api_key
        masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"
    return ModelConfigResponse(
        provider=current.provider,
        model=current.model,
        temperature=current.temperature,
        maxTokens=current.max_tokens,
        hasApiKey=bool(current.api_key),
        apiKeyMasked=masked,
    )


@router.put("/config")
async def update_model_config(req: UpdateModelConfigRequest) -> ModelConfigResponse:
    try:
        updated = get_model_preference_store().update(
            provider=req.provider,
            model=req.model,
            temperature=req.temperature,
            max_tokens=req.maxTokens,
            api_key=req.apiKey,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ModelConfigResponse(
        provider=updated.provider,
        model=updated.model,
        temperature=updated.temperature,
        maxTokens=updated.max_tokens,
        hasApiKey=bool(updated.api_key),
        apiKeyMasked=(f"{updated.api_key[:4]}...{updated.api_key[-4:]}" if len(updated.api_key) > 8 else ("****" if updated.api_key else None)),
    )
