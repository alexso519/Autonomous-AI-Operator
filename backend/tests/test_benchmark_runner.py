"""Tests for benchmark task definitions and runner helpers."""

from app.benchmark.benchmark_tasks import BENCHMARK_TASKS, all_categories, get_task
from app.benchmark.benchmark_runner import BenchmarkRunner


def test_all_categories_covered():
    cats = all_categories()
    assert "research" in cats
    assert "tool_usage" in cats
    assert len(BENCHMARK_TASKS) >= 8


def test_get_task():
    task = get_task("research_web_topic")
    assert task is not None
    assert task.category == "research"
    assert task.expects_tools


def test_evaluate_criteria_min_quality():
    task = get_task("summarize_bullets")
    assert task is not None
    assert BenchmarkRunner._evaluate_criteria(task, {"qualityScore": 0.6})
    assert not BenchmarkRunner._evaluate_criteria(task, {"qualityScore": 0.3})


def test_summarize_suite():
    results = [
        {"success": True, "durationSeconds": 10, "retryCount": 0, "hallucinationRisk": 0.1},
        {"success": False, "durationSeconds": 20, "retryCount": 1, "hallucinationRisk": 0.3},
    ]
    summary = BenchmarkRunner._summarize_suite(results)
    assert summary["total"] == 2
    assert summary["successes"] == 1
    assert summary["successRate"] == 50.0
