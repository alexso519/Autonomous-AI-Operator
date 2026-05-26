"""
Tests for UI navigation graph.
Run with: python -m pytest backend/tests/test_ui_navigation.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.computer_use.ui_navigation_graph import UINavigationGraph


def test_navigation_graph_records_transitions():
    graph = UINavigationGraph()
    graph.add_state("hash_a", label="Home")
    graph.record_transition("hash_a", "hash_b", "click:Submit", success=True)
    graph.add_state("hash_b", label="Confirmation")
    assert graph.to_dict()["edgeCount"] == 1
    assert graph.to_dict()["nodeCount"] == 2


def test_navigation_graph_plan_path():
    graph = UINavigationGraph()
    graph.add_state("home", label="Home Screen")
    graph.record_transition("home", "settings", "click:Settings")
    graph.add_state("settings", label="Settings Page")
    path = graph.plan_path("Settings")
    assert len(path) >= 1
