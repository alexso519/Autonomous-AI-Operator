"""
CrewAI agent factory — converts canvas node data into CrewAI Agent instances.

Mapping:
  Canvas AgentNodeData  →  CrewAI Agent
  ───────────────────────────────────────
  role                  →  role
  goal                  →  goal
  backstory             →  backstory (auto-generated if absent)
  model                 →  llm model string
  temperature           →  temperature

Each agent gets a unique instance from the pool so concurrent runs don't clash.
"""

import logging
from typing import Any

from crewai import Agent as CrewAgent
from langchain_community.chat_models import ChatOllama

from app.config.settings import settings

logger = logging.getLogger(__name__)


# ── Default backstories per role prefix ─────────────────────────
# If a node has no backstory, we generate one from its role name.
_DEFAULT_BACKSTORIES: dict[str, str] = {
    "researcher": (
        "You are a meticulous research analyst. "
        "You gather facts, verify sources, and present evidence-based findings."
    ),
    "writer": (
        "You are a skilled content strategist. "
        "You transform raw information into clear, compelling narratives."
    ),
    "reviewer": (
        "You are a thorough quality assurance lead. "
        "You catch inconsistencies, validate logic, and ensure high standards."
    ),
    "planner": (
        "You are an organised project coordinator. "
        "You break complex goals into structured, actionable steps."
    ),
    "coder": (
        "You are an experienced software engineer. "
        "You write clean, documented, correct code."
    ),
    "analyst": (
        "You are a data-driven analyst. "
        "You extract patterns, identify trends, and produce actionable insights."
    ),
}


def _infer_backstory(role: str, goal: str) -> str:
    """Generate a default backstory from the role name if none provided."""
    role_lower = role.lower()
    for key, story in _DEFAULT_BACKSTORIES.items():
        if key in role_lower:
            return story
    return f"You are a {role}. Your goal is: {goal}"


def build_llm(model_override: str | None = None) -> ChatOllama:
    """
    Build a LangChain ChatOllama instance pointing at the local Ollama server.

    Uses OpenAI-compatible endpoint so CrewAI's built-in LLM support works
    without custom subclasses.
    """
    model = model_override or settings.ollama_model
    return ChatOllama(
        model=model,
        base_url=settings.ollama_url,
        temperature=0.7,
    )


def build_agent(node_data: dict[str, Any]) -> CrewAgent:
    """
    Convert a canvas node's data dict into a CrewAI Agent.

    Expected node_data keys:
        role (str)           — Agent's professional role
        goal (str)           — Agent's objective for this task
        backstory (str, opt) — Agent's background narrative
        model (str, opt)     — Ollama model override
        temperature (float)  — LLM temperature (default 0.7)
        label (str)          — Display name (used for agent name)

    Returns a ready-to-use CrewAI Agent.
    """
    role = node_data.get("role", "Assistant")
    goal = node_data.get("goal", "Complete the assigned task.")
    backstory = node_data.get("backstory") or _infer_backstory(role, goal)
    temperature = node_data.get("temperature", 0.7)
    model_override = node_data.get("model")
    label = node_data.get("label", role)

    llm = build_llm(model_override)

    agent = CrewAgent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        temperature=temperature,
        verbose=True,
        allow_delegation=False,
    )

    logger.info(
        "Built agent: %s | role=%s | model=%s | temp=%s",
        label,
        role,
        model_override or settings.ollama_model,
        temperature,
    )

    return agent