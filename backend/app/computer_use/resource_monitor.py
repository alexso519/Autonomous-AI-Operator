"""
Resource monitor — track resource pressure during desktop automation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResourceSnapshot:
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    disk_free_mb: float = 0.0
    pressure_level: str = "normal"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpuPercent": round(self.cpu_percent, 1),
            "memoryMb": round(self.memory_mb, 1),
            "diskFreeMb": round(self.disk_free_mb, 1),
            "pressureLevel": self.pressure_level,
            "warnings": self.warnings,
        }


class ResourceMonitor:
    """Monitor system resource pressure during desktop sessions."""

    _history: dict[str, list[ResourceSnapshot]] = {}

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id

    def snapshot(self) -> ResourceSnapshot:
        snap = ResourceSnapshot()
        try:
            import psutil  # lazy import
            snap.cpu_percent = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            snap.memory_mb = mem.used / (1024 * 1024)
            disk = psutil.disk_usage("/")
            snap.disk_free_mb = disk.free / (1024 * 1024)

            if snap.cpu_percent > 90:
                snap.warnings.append("high_cpu")
                snap.pressure_level = "high"
            elif snap.memory_mb > 8000:
                snap.warnings.append("high_memory")
                snap.pressure_level = "elevated"
            elif snap.disk_free_mb < 500:
                snap.warnings.append("low_disk")
                snap.pressure_level = "elevated"

        except ImportError:
            snap.warnings.append("psutil_unavailable")
        except Exception as exc:
            logger.debug("Resource snapshot failed: %s", exc)
            snap.warnings.append("snapshot_failed")

        self._history.setdefault(self.execution_id, []).append(snap)
        return snap

    def is_under_pressure(self) -> bool:
        snap = self.snapshot()
        return snap.pressure_level in ("high", "elevated")

    def to_dict(self) -> dict[str, Any]:
        history = self._history.get(self.execution_id, [])
        latest = history[-1] if history else ResourceSnapshot()
        return {
            "executionId": self.execution_id,
            "latest": latest.to_dict(),
            "sampleCount": len(history),
        }

    @classmethod
    def cleanup(cls, execution_id: str) -> None:
        cls._history.pop(execution_id, None)
