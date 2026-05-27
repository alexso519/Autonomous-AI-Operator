"""
Tongyi execution adapter compatible with the engine's agent.execute_task contract.
"""

from __future__ import annotations

import os

import httpx

from app.config.locale import language_instruction, t

DEFAULT_TONGYI_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


class TongyiAgentAdapter:
    """Minimal adapter with CrewAI-like execute_task(task) API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        role: str,
        goal: str,
        backstory: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = (
            base_url
            or os.getenv("TONGYI_BASE_URL", DEFAULT_TONGYI_BASE_URL)
        ).rstrip("/")

    def execute_task(self, task: object) -> str:
        description = getattr(task, "description", "") or str(task)
        system_prompt = (
            f"Role: {self.role}\n"
            f"Goal: {self.goal}\n"
            f"Backstory: {self.backstory}\n\n"
            f"{language_instruction()}\n"
            f"{t('follow_instructions')}"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": description},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=120.0) as client:
            res = client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            if res.status_code >= 400:
                raise RuntimeError(
                    f"Tongyi API request failed ({res.status_code}): {res.text[:300]}"
                )
            body = res.json()
        choices = body.get("choices") or []
        if not choices:
            return "(no output)"
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        return str(content) if content else "(no output)"
