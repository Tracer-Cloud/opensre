"""LangGraph / LLM routing benchmarks."""

from tests.benchmarks.toolcall_model_benchmark.pricing import estimate_run_cost_usd


def run_benchmark(*args, **kwargs):
    """Lazy wrapper to avoid eager benchmark module import side effects."""
    from tests.benchmarks.toolcall_model_benchmark.benchmark_generator import run_benchmark as _run
    return _run(*args, **kwargs)


__all__ = ["estimate_run_cost_usd", "run_benchmark"]
