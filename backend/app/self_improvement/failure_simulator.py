"""
Failure simulator — synthetic failure generation for resilience validation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class FailureSimulator:
    @classmethod
    def simulate(cls, scenario_type: str) -> dict[str, Any]:
        simulators = {
            "retry_storm": cls._retry_storm,
            "hallucination_stress": cls._hallucination_stress,
            "coordination_collapse": cls._coordination_collapse,
            "tool_cascade": cls._tool_cascade,
            "degraded_mode": cls._degraded_mode,
        }
        fn = simulators.get(scenario_type, cls._generic_failure)
        return fn()

    @classmethod
    def _retry_storm(cls) -> dict[str, Any]:
        return {
            "simulationType": "retry_storm",
            "injectedFailures": 6,
            "backoffMs": [100, 200, 400, 800, 1600, 3200],
            "expectedRecovery": "circuit_breaker_or_backoff_cap",
            "confidence": 0.85,
        }

    @classmethod
    def _hallucination_stress(cls) -> dict[str, Any]:
        return {
            "simulationType": "hallucination_stress",
            "groundingRequired": True,
            "missingToolProbability": 0.4,
            "expectedBehavior": "defer_or_request_tools",
            "confidence": 0.8,
        }

    @classmethod
    def _coordination_collapse(cls) -> dict[str, Any]:
        return {
            "simulationType": "coordination_collapse",
            "conflictingAgents": 3,
            "consensusTimeoutMs": 5000,
            "expectedBehavior": "arbitration_or_sequential_fallback",
            "confidence": 0.78,
        }

    @classmethod
    def _tool_cascade(cls) -> dict[str, Any]:
        return {
            "simulationType": "tool_cascade",
            "primaryToolFailure": True,
            "fallbackTools": ["web_search", "file_read"],
            "expectedBehavior": "graceful_degradation",
            "confidence": 0.82,
        }

    @classmethod
    def _degraded_mode(cls) -> dict[str, Any]:
        return {
            "simulationType": "degraded_mode",
            "disabledCapabilities": ["vector_memory", "parallel_execution"],
            "expectedBehavior": "single_path_completion",
            "confidence": 0.77,
        }

    @classmethod
    def _generic_failure(cls) -> dict[str, Any]:
        return {
            "simulationType": "generic",
            "injectedFailures": 1,
            "confidence": 0.7,
        }
