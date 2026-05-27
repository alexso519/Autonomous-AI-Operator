"""
Server-side model preference management for frontend controls.

Keeps model/provider selection in backend memory so API keys and policy stay
server-side. This does not expose secrets to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.config.settings import settings


def _parse_allowed_models() -> list[str]:
    raw = os.getenv("ALLOWED_MODELS", "").strip()
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models
    # Sensible defaults for local Ollama and Tongyi/Qwen naming.
    return [
        settings.ollama_model,
        "qwen-plus",
        "qwen-turbo",
        "qwen-max",
        "qwen3.6-flash",
    ]


DEFAULT_TONGYI_MODEL = "qwen-turbo"
DEFAULT_TONGYI_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


@dataclass
class ModelPreferences:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    api_key: str
    base_url: str


class ModelPreferenceStore:
    _allowed_providers = {"ollama", "tongyi"}

    def __init__(self) -> None:
        provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower() or "ollama"
        if provider not in self._allowed_providers:
            provider = "ollama"
        default_model = os.getenv("DEFAULT_MODEL", "").strip()
        if not default_model:
            default_model = (
                DEFAULT_TONGYI_MODEL if provider == "tongyi" else settings.ollama_model
            )
        self._allowed_models = _parse_allowed_models()
        self._state = ModelPreferences(
            provider=provider,
            model=default_model,
            temperature=0.7,
            max_tokens=4096,
            api_key=os.getenv("TONGYI_API_KEY", "").strip(),
            base_url=os.getenv("TONGYI_BASE_URL", DEFAULT_TONGYI_BASE_URL).strip()
            or DEFAULT_TONGYI_BASE_URL,
        )
        if self._state.model not in self._allowed_models:
            self._allowed_models.append(self._state.model)

    def get_options(self) -> dict[str, list[str]]:
        return {
            "providers": sorted(self._allowed_providers),
            "models": self._allowed_models,
        }

    def get(self) -> ModelPreferences:
        return self._state

    def update(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        api_key: str | None = None,
    ) -> ModelPreferences:
        if provider is not None:
            provider = provider.strip().lower()
            if provider not in self._allowed_providers:
                raise ValueError("Unsupported provider")
            self._state.provider = provider

        if model is not None:
            model = model.strip()
            if not model:
                raise ValueError("Model cannot be empty")
            if model not in self._allowed_models:
                raise ValueError("Model is not in allowed list")
            self._state.model = model

        if temperature is not None:
            if temperature < 0 or temperature > 2:
                raise ValueError("Temperature must be between 0 and 2")
            self._state.temperature = temperature

        if max_tokens is not None:
            if max_tokens < 128 or max_tokens > 65536:
                raise ValueError("Max tokens must be between 128 and 65536")
            self._state.max_tokens = max_tokens

        if api_key is not None:
            self._state.api_key = api_key.strip()

        return self._state


_store: ModelPreferenceStore | None = None


def get_model_preference_store() -> ModelPreferenceStore:
    global _store
    if _store is None:
        _store = ModelPreferenceStore()
    return _store
