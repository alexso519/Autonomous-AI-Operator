"""
Task-aware code planner — generate edit plans from repo analysis.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.coding.repo_analyzer import RepoAnalysis


@dataclass
class PlannedEdit:
    file_path: str
    action: str  # modify | create | delete
    description: str
    priority: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "filePath": self.file_path,
            "action": self.action,
            "description": self.description,
            "priority": self.priority,
        }


@dataclass
class EditPlan:
    plan_id: str
    objective: str
    target_files: list[str]
    edits: list[PlannedEdit]
    test_command: str
    rationale: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "planId": self.plan_id,
            "objective": self.objective,
            "targetFiles": self.target_files,
            "edits": [e.to_dict() for e in self.edits],
            "testCommand": self.test_command,
            "rationale": self.rationale,
            "confidence": round(self.confidence, 3),
        }


class CodePlanner:
    """Generate edit plans from repository analysis and task objective."""

    FIX_KEYWORDS = re.compile(
        r"\b(fix|debug|failing|broken|error|bug|test|patch|repair)\b",
        re.I,
    )

    @classmethod
    def plan(
        cls,
        analysis: RepoAnalysis,
        objective: str,
    ) -> EditPlan:
        target_files = list(analysis.relevant_files)
        edits: list[PlannedEdit] = []

        is_fix_task = bool(cls.FIX_KEYWORDS.search(objective))

        if is_fix_task and analysis.test_paths:
            for tp in analysis.test_paths[:5]:
                edits.append(PlannedEdit(
                    file_path=tp,
                    action="modify",
                    description=f"Investigate and fix failing tests in {tp}",
                    priority=1,
                ))
            for src in target_files:
                if not any(t in src for t in analysis.test_paths):
                    edits.append(PlannedEdit(
                        file_path=src,
                        action="modify",
                        description=f"Review source file {src} for root cause",
                        priority=2,
                    ))
        else:
            for i, src in enumerate(target_files[:8]):
                edits.append(PlannedEdit(
                    file_path=src,
                    action="modify",
                    description=f"Apply changes for: {objective[:80]}",
                    priority=i + 1,
                ))

        test_cmd = cls._infer_test_command(analysis)
        confidence = 0.7 if analysis.test_files else 0.5
        if is_fix_task and analysis.test_paths:
            confidence = min(0.85, confidence + 0.1)

        rationale_parts = [
            f"Analyzed {analysis.code_files} source files, {analysis.test_files} test files",
        ]
        if analysis.languages:
            top_lang = max(analysis.languages, key=analysis.languages.get)
            rationale_parts.append(f"Primary language: {top_lang}")

        return EditPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            objective=objective,
            target_files=target_files,
            edits=edits,
            test_command=test_cmd,
            rationale="; ".join(rationale_parts),
            confidence=confidence,
        )

    @classmethod
    def _infer_test_command(cls, analysis: RepoAnalysis) -> str:
        if analysis.test_paths:
            if any(p.endswith(".py") for p in analysis.test_paths):
                return "python -m pytest -q --tb=short"
        if (analysis.root / "package.json").exists():
            return "npm test"
        return "python -m pytest -q --tb=short"
