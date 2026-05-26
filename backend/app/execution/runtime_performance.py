"""
Runtime performance optimizer — stage timing, bottlenecks, recommendations.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.database.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class StageTiming:
    stage: str
    duration_seconds: float
    agent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimePerformanceTracker:
    """Per-execution performance instrumentation."""

    _active: dict[str, dict[str, Any]] = {}
    _stage_starts: dict[str, dict[str, float]] = {}

    @classmethod
    def start_execution(cls, execution_id: str) -> None:
        cls._active[execution_id] = {
            "started_at": time.monotonic(),
            "stages": [],
            "idle_seconds": 0.0,
            "tool_durations": [],
            "reflection_seconds": 0.0,
            "synthesis_seconds": 0.0,
            "agent_durations": [],
        }
        cls._stage_starts[execution_id] = {}

    @classmethod
    def begin_stage(cls, execution_id: str, stage: str, agent: str = "") -> None:
        if execution_id not in cls._stage_starts:
            cls.start_execution(execution_id)
        cls._stage_starts.setdefault(execution_id, {})[stage] = time.monotonic()

    @classmethod
    def end_stage(
        cls,
        execution_id: str,
        stage: str,
        agent: str = "",
        **metadata: Any,
    ) -> float:
        starts = cls._stage_starts.get(execution_id, {})
        start = starts.pop(stage, None)
        if start is None:
            return 0.0
        duration = time.monotonic() - start
        active = cls._active.setdefault(execution_id, {"stages": []})
        active["stages"].append(
            {"stage": stage, "duration": duration, "agent": agent, **metadata}
        )

        if stage == "agent":
            active.setdefault("agent_durations", []).append(
                {"agent": agent, "duration": duration}
            )
        elif stage == "tool":
            active.setdefault("tool_durations", []).append(
                {"tool": metadata.get("tool", ""), "duration": duration}
            )
        elif stage == "reflection":
            active["reflection_seconds"] = active.get("reflection_seconds", 0) + duration
        elif stage == "synthesis":
            active["synthesis_seconds"] = active.get("synthesis_seconds", 0) + duration
        elif stage == "idle":
            active["idle_seconds"] = active.get("idle_seconds", 0) + duration
        return duration

    @classmethod
    def record_idle(cls, execution_id: str, seconds: float) -> None:
        active = cls._active.setdefault(execution_id, {})
        active["idle_seconds"] = active.get("idle_seconds", 0) + seconds

    @classmethod
    def finalize(cls, execution_id: str) -> dict[str, Any]:
        active = cls._active.pop(execution_id, {})
        cls._stage_starts.pop(execution_id, None)
        if not active:
            return {}

        total = time.monotonic() - active.get("started_at", time.monotonic())
        stages = active.get("stages", [])
        agent_durs = active.get("agent_durations", [])
        tool_durs = active.get("tool_durations", [])

        slowest_agents = sorted(agent_durs, key=lambda x: x["duration"], reverse=True)[:5]
        slowest_tools = sorted(tool_durs, key=lambda x: x["duration"], reverse=True)[:5]

        bottlenecks = cls._detect_bottlenecks(active, total)
        recommendations = cls._generate_recommendations(active, bottlenecks)

        return {
            "executionId": execution_id,
            "totalDurationSeconds": round(total, 2),
            "idleSeconds": round(active.get("idle_seconds", 0), 2),
            "reflectionSeconds": round(active.get("reflection_seconds", 0), 2),
            "synthesisSeconds": round(active.get("synthesis_seconds", 0), 2),
            "slowestAgents": slowest_agents,
            "slowestTools": slowest_tools,
            "bottlenecks": bottlenecks,
            "recommendations": recommendations,
            "stageCount": len(stages),
        }

    @classmethod
    def _detect_bottlenecks(cls, active: dict[str, Any], total: float) -> list[dict[str, Any]]:
        bottlenecks: list[dict[str, Any]] = []
        if total <= 0:
            return bottlenecks

        reflection = active.get("reflection_seconds", 0)
        if reflection / total > 0.15:
            bottlenecks.append({
                "type": "reflection_overhead",
                "severity": "medium",
                "detail": f"Reflection consumed {reflection:.1f}s ({reflection/total:.0%} of runtime)",
            })

        synthesis = active.get("synthesis_seconds", 0)
        if synthesis / total > 0.1:
            bottlenecks.append({
                "type": "synthesis_latency",
                "severity": "low",
                "detail": f"Synthesis took {synthesis:.1f}s",
            })

        idle = active.get("idle_seconds", 0)
        if idle / total > 0.2:
            bottlenecks.append({
                "type": "agent_idle",
                "severity": "high",
                "detail": f"Agent idle/wait time {idle:.1f}s ({idle/total:.0%})",
            })

        for td in active.get("tool_durations", []):
            if td.get("duration", 0) > 30:
                bottlenecks.append({
                    "type": "slow_tool",
                    "severity": "medium",
                    "detail": f"Tool {td.get('tool')} took {td['duration']:.1f}s",
                })

        agent_durs = active.get("agent_durations", [])
        if agent_durs:
            max_agent = max(agent_durs, key=lambda x: x["duration"])
            if max_agent["duration"] / total > 0.5:
                bottlenecks.append({
                    "type": "slow_agent",
                    "severity": "high",
                    "detail": f"Agent {max_agent['agent']} dominated runtime ({max_agent['duration']:.1f}s)",
                })
        return bottlenecks

    @classmethod
    def _generate_recommendations(
        cls,
        active: dict[str, Any],
        bottlenecks: list[dict[str, Any]],
    ) -> list[str]:
        recs: list[str] = []
        types = {b["type"] for b in bottlenecks}

        if "agent_idle" in types:
            recs.append("Reduce context size or enable tighter token budgets to speed local model inference.")
        if "reflection_overhead" in types:
            recs.append("Skip reflection on recovery nodes; quality threshold may be too aggressive.")
        if "slow_tool" in types:
            recs.append("Enable tool result caching for repeated queries; batch tool calls where possible.")
        if "slow_agent" in types:
            recs.append("Route lightweight steps to smaller model tier; compress older agent outputs.")
        if "synthesis_latency" in types:
            recs.append("Use synthesis-focused compression — pass condensed findings only.")
        if not recs:
            recs.append("Runtime within normal bounds — no critical optimizations detected.")
        return recs

    @classmethod
    async def persist_snapshot(cls, execution_id: str, snapshot: dict[str, Any]) -> None:
        if not snapshot:
            return
        try:
            db = await get_db()
            now = datetime.now(timezone.utc).isoformat()
            await db.execute(
                """INSERT INTO runtime_performance_snapshots
                   (id, execution_id, snapshot_data, created_at)
                   VALUES (?, ?, ?, ?)""",
                (uuid.uuid4().hex[:16], execution_id, json.dumps(snapshot), now),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("Performance snapshot persist failed: %s", exc)

    @classmethod
    async def get_execution_performance(cls, execution_id: str) -> dict[str, Any]:
        try:
            db = await get_db()
            cursor = await db.execute(
                """SELECT snapshot_data FROM runtime_performance_snapshots
                   WHERE execution_id = ? ORDER BY created_at DESC LIMIT 1""",
                (execution_id,),
            )
            row = await cursor.fetchone()
            if row:
                return json.loads(row["snapshot_data"])
        except Exception as exc:
            logger.warning("Performance fetch failed: %s", exc)
        return {}

    @classmethod
    async def get_aggregate_performance(cls, days: int = 14) -> dict[str, Any]:
        try:
            db = await get_db()
            cursor = await db.execute(
                f"""SELECT snapshot_data FROM runtime_performance_snapshots
                    WHERE created_at > datetime('now', '-{int(days)} days')
                    ORDER BY created_at DESC LIMIT 200"""
            )
            rows = await cursor.fetchall()
            if not rows:
                return {"samples": 0}

            totals, reflections, syntheses, retries = [], [], [], []
            bottleneck_counts: dict[str, int] = {}

            for row in rows:
                data = json.loads(row["snapshot_data"])
                totals.append(data.get("totalDurationSeconds", 0))
                reflections.append(data.get("reflectionSeconds", 0))
                syntheses.append(data.get("synthesisSeconds", 0))
                for b in data.get("bottlenecks", []):
                    t = b.get("type", "unknown")
                    bottleneck_counts[t] = bottleneck_counts.get(t, 0) + 1

            return {
                "samples": len(rows),
                "avgDuration": round(sum(totals) / len(totals), 2) if totals else 0,
                "avgReflectionOverhead": round(sum(reflections) / len(reflections), 2) if reflections else 0,
                "avgSynthesisLatency": round(sum(syntheses) / len(syntheses), 2) if syntheses else 0,
                "bottleneckCounts": bottleneck_counts,
            }
        except Exception as exc:
            logger.warning("Aggregate performance failed: %s", exc)
            return {"samples": 0}
