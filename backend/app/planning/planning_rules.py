from app.services.task_analyzer import RiskLevel, TaskAnalysis


def requires_workflow_approval(analysis: TaskAnalysis) -> bool:
    """Determine whether the generated workflow requires an approval gate."""
    return analysis.risk_level != RiskLevel.LOW


def get_approval_reason(analysis: TaskAnalysis) -> str:
    """Provide a deterministic approval prompt reason based on task risk."""
    if analysis.risk_level == RiskLevel.HIGH:
        return (
            "High-risk operation detected. Review the task and confirm execution "
            "before the workflow proceeds."
        )
    elif analysis.risk_level == RiskLevel.MEDIUM:
        return (
            "Medium-risk task detected. Human approval is required before running "
            "the generated workflow."
        )

    return "Human approval required before executing the workflow."
