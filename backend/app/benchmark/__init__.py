"""Benchmark evaluation suite."""

__all__ = ["BenchmarkRunner", "run_benchmarks_on_startup"]


def __getattr__(name: str):
    if name in __all__:
        from app.benchmark import benchmark_runner as _br
        return getattr(_br, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
