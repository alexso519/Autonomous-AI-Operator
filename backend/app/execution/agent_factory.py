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
from app.config.locale import language_instruction, t
from app.execution.model_preferences import get_model_preference_store
from app.execution.tongyi_agent import TongyiAgentAdapter

logger = logging.getLogger(__name__)


_DEFAULT_BACKSTORIES_ZH: dict[str, str] = {
    "researcher": "backstory_researcher",
    "writer": "backstory_writer",
    "reviewer": "backstory_reviewer",
    "planner": "backstory_planner",
    "coder": "backstory_coder",
    "analyst": "backstory_analyst",
}

_DEFAULT_BACKSTORIES_EN: dict[str, str] = {
    "researcher": "backstory_researcher",
    "writer": "backstory_writer",
    "reviewer": "backstory_reviewer",
    "planner": "backstory_planner",
    "coder": "backstory_coder",
    "analyst": "backstory_analyst",
}


def _infer_backstory(role: str, goal: str) -> str:
    """Generate a default backstory from the role name if none provided."""
    from app.config.locale import is_zh_hk

    role_lower = role.lower()
    mapping = _DEFAULT_BACKSTORIES_ZH if is_zh_hk() else _DEFAULT_BACKSTORIES_EN
    for key, story_key in mapping.items():
        if key in role_lower:
            return t(story_key)
    return t("backstory_default", role=role, goal=goal)


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


async def build_agent_routed(
    node_data: dict[str, Any],
    *,
    execution_id: str,
    node_id: str,
    agent_name: str,
    task_complexity: str = "moderate",
    context_text: str = "",
    needs_tools: bool = False,
    is_synthesis: bool = False,
    node_index: int = 1,
    total_nodes: int = 1,
    emit_fn: Any = None,
) -> tuple[Any, str]:
    """Build agent with adaptive model routing."""
    store = get_model_preference_store()
    current_cfg = store.get()
    selected_provider = (
        str(node_data.get("forcedProvider")).strip().lower()
        if node_data.get("forcedProvider") is not None
        else current_cfg.provider
    )

    if selected_provider == "tongyi":
        selected_model = (
            str(node_data.get("forcedModel")).strip()
            if node_data.get("forcedModel")
            else current_cfg.model
        )
        selected_temperature = float(node_data.get("temperature", current_cfg.temperature))
        selected_max_tokens = int(node_data.get("maxTokens", current_cfg.max_tokens))
        api_key = current_cfg.api_key.strip()
        if not api_key:
            raise RuntimeError(t("api_key_missing_error"))
        if emit_fn and execution_id:
            await emit_fn(
                execution_id,
                "model_selected",
                agent_name,
                t("model_selected_tongyi", model=selected_model),
                nodeId=node_id,
                model=selected_model,
                tier="tongyi",
                reasons=["provider_tongyi_selected"],
            )
        role = node_data.get("role", "Assistant")
        goal = node_data.get("goal", "Complete the assigned task.")
        backstory = node_data.get("backstory") or _infer_backstory(role, goal)
        backstory = f"{backstory}\n\n{language_instruction()}"
        return (
            TongyiAgentAdapter(
                api_key=api_key,
                model=selected_model,
                role=role,
                goal=goal,
                backstory=backstory,
                temperature=selected_temperature,
                max_tokens=selected_max_tokens,
                base_url=current_cfg.base_url,
            ),
            selected_model,
        )

    forced_model = node_data.get("forcedModel")
    if isinstance(forced_model, str) and forced_model.strip():
        selected = forced_model.strip()
        if emit_fn and execution_id:
            await emit_fn(
                execution_id,
                "model_selected",
                agent_name,
                t("model_forced", model=selected),
                nodeId=node_id,
                model=selected,
                tier="manual_override",
                reasons=["frontend_model_override"],
            )
        enriched = {**node_data, "model": selected}
        return build_agent(enriched), selected

    from app.execution.model_router import ModelRouter, RoutingContext

    routing_ctx = RoutingContext(
        task_complexity=task_complexity,
        context_token_estimate=ModelRouter.estimate_context_tokens(context_text),
        needs_tools=needs_tools,
        is_synthesis=is_synthesis,
        is_final_output=node_index >= total_nodes,
        agent_role=node_data.get("role", ""),
        node_index=node_index,
        total_nodes=total_nodes,
    )
    decision = await ModelRouter.select_model(
        routing_ctx,
        execution_id=execution_id,
        emit_fn=emit_fn,
        node_id=node_id,
        agent_name=agent_name,
    )
    enriched = {**node_data, "model": decision.model}
    return build_agent(enriched), decision.model


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
    role = node_data.get("role", t("default_role"))
    goal = node_data.get("goal", t("default_task"))
    backstory = node_data.get("backstory") or _infer_backstory(role, goal)
    backstory = f"{backstory}\n\n{language_instruction()}"
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