"""
Quick test of TaskAnalyzer functionality.

Run with: python -m pytest backend/tests/test_task_analyzer.py -v
Or: python backend/tests/test_task_analyzer.py
"""

import asyncio
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.task_analyzer import (
    TaskAnalyzer,
    TaskType,
    Complexity,
    RiskLevel,
)


async def test_task_analyzer():
    """Test TaskAnalyzer on various objectives."""
    analyzer = TaskAnalyzer()

    test_cases = [
        {
            "objective": "Research the current state of AI chip market and write a business report",
            "expected_type": TaskType.RESEARCH,
        },
        {
            "objective": "Analyze our sales data to identify trends and recommend strategy",
            "expected_type": TaskType.ANALYSIS,
        },
        {
            "objective": "Write a blog post about machine learning for beginners",
            "expected_type": TaskType.CONTENT_CREATION,
        },
        {
            "objective": "Build a Python function that processes JSON files",
            "expected_type": TaskType.CODING,
        },
        {
            "objective": "Create a project plan for launching our new product",
            "expected_type": TaskType.PLANNING,
        },
    ]

    print("\n" + "=" * 80)
    print("TaskAnalyzer Test Suite")
    print("=" * 80 + "\n")

    for i, test in enumerate(test_cases, 1):
        objective = test["objective"]
        expected_type = test["expected_type"]

        print(f"Test {i}: {objective[:60]}...")
        try:
            analysis = await analyzer.analyze(objective)

            print(f"  ✓ Task Type: {analysis.task_type.value} (expected: {expected_type.value})")
            print(f"    Complexity: {analysis.complexity.value}")
            print(f"    Risk Level: {analysis.risk_level.value}")
            print(f"    Agents: {len(analysis.required_agents)}")
            print(f"    Keywords: {', '.join(analysis.keywords[:5])}")
            print(f"    Approval Required: {analysis.requires_human_approval}")
            print()

            # Check if type matches
            if analysis.task_type == expected_type:
                print(f"  ✅ Type match!")
            else:
                print(f"  ⚠️  Type mismatch (got {analysis.task_type}, expected {expected_type})")

            print()

        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            print()

    print("=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_task_analyzer())
