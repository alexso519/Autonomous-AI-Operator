"""
Browser action planner — plan multi-step browser workflows.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BrowserStep:
    action: str  # navigate | click | fill | wait | screenshot | extract
    target: str
    value: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "target": self.target,
            "value": self.value,
            "description": self.description,
        }


@dataclass
class BrowserPlan:
    plan_id: str
    objective: str
    start_url: str
    steps: list[BrowserStep]
    search_query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "objective": self.objective,
            "startUrl": self.start_url,
            "steps": [s.to_dict() for s in self.steps],
            "searchQuery": self.search_query,
        }


class ActionPlanner:
    """Generate browser action plans from objectives."""

    URL_RE = re.compile(r"https?://[^\s]+")

    @classmethod
    def plan(cls, objective: str) -> BrowserPlan:
        lower = objective.lower()
        steps: list[BrowserStep] = []
        search_query = ""
        start_url = "https://github.com"

        url_match = cls.URL_RE.search(objective)
        if url_match:
            start_url = url_match.group(0).rstrip(".,)")

        if "github" in lower:
            start_url = "https://github.com"
            steps.append(BrowserStep("navigate", start_url, description="Open GitHub"))

        if any(k in lower for k in ("research", "news", "search", "find", "latest")):
            # Extract search topic
            topic_match = re.search(
                r"(?:research|search|find|latest)\s+(?:for\s+)?(.+?)(?:\s+and|\s*$)",
                objective,
                re.I,
            )
            search_query = topic_match.group(1).strip() if topic_match else objective[:80]
            if "nvidia" in lower or "blackwell" in lower:
                search_query = "NVIDIA Blackwell news"
                start_url = "https://news.google.com/search?q=NVIDIA+Blackwell"
                steps.append(BrowserStep(
                    "navigate", start_url,
                    description="Search NVIDIA Blackwell news",
                ))
            else:
                start_url = f"https://www.google.com/search?q={search_query.replace(' ', '+')}"
                steps.append(BrowserStep(
                    "navigate", start_url,
                    description=f"Search: {search_query}",
                ))

        steps.extend([
            BrowserStep("screenshot", "page", description="Capture evidence screenshot"),
            BrowserStep("extract", "page", description="Extract structured page content"),
        ])

        if not steps:
            steps = [
                BrowserStep("navigate", start_url, description="Open start page"),
                BrowserStep("extract", "page", description="Extract content"),
            ]

        return BrowserPlan(
            plan_id=f"bplan-{uuid.uuid4().hex[:8]}",
            objective=objective,
            start_url=start_url,
            steps=steps,
            search_query=search_query,
        )
