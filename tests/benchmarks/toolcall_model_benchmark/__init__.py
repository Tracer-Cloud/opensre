"""LangGraph / LLM routing benchmarks."""

from tests.benchmarks.toolcall_model_benchmark.pricing import estimate_run_cost_usd


def run_benchmark(*args, **kwargs):
    """Lazy wrapper to avoid eager benchmark module import side effects."""
    from tests.benchmarks.toolcall_model_benchmark.benchmark_generator import run_benchmark as _run

    return _run(*args, **kwargs)


def update_readme_benchmarks(*args, **kwargs):
    """Lazy wrapper to avoid eager import of readme_updater."""
    from tests.benchmarks.toolcall_model_benchmark.readme_updater import (
        update_readme_benchmarks as _update,
    )

    return _update(*args, **kwargs)


__all__ = ["estimate_run_cost_usd", "run_benchmark", "update_readme_benchmarks"]
