from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

from app.config import LLMSettings
from tests.benchmarks.toolcall_model_benchmark.pipeline_benchmark import (
    configure_split_models,
    estimate_run_cost_usd,
    get_fixture_by_id,
    run_langgraph_investigation_bench,
)
from tests.benchmarks.toolcall_model_benchmark.pricing import (
    DEFAULT_REASONING_USD_PER_MTOK,
    DEFAULT_TOOL_USD_PER_MTOK,
)

FIXED_SCENARIO_IDS: tuple[str, ...] = (
    "001-replication-lag",
    "002-connection-exhaustion",
    "003-storage-full",
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaseMetrics:
    """Per-case benchmark measurements for one scenario run."""

    scenario_id: str
    run_status: Literal["ok", "error"]
    duration_seconds: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    error: str = ""


@dataclass(frozen=True)
class SummaryMetrics:
    """Aggregate totals and averages across all executed cases."""

    case_count: int
    success_count: int
    error_count: int
    total_duration_seconds: float
    avg_duration_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_estimated_cost_usd: float
    avg_estimated_cost_usd: float


def _resolve_models() -> tuple[str, str]:
    """Resolve reasoning/tool model IDs from active provider environment settings."""
    settings = LLMSettings.from_env()
    provider = settings.provider
    reasoning_attr = f"{provider}_reasoning_model"
    tool_attr = f"{provider}_toolcall_model"
    reasoning_model = getattr(settings, reasoning_attr, None)
    tool_model = getattr(settings, tool_attr, None)
    if reasoning_model is None or tool_model is None:
        raise ValueError(
            f"Provider {provider!r} is missing attributes {reasoning_attr!r} "
            f"or {tool_attr!r} on LLMSettings."
        )
    return str(reasoning_model), str(tool_model)


def _summarize(cases: list[CaseMetrics]) -> SummaryMetrics:
    """Compute benchmark summary totals and averages from per-case data."""
    case_count = len(cases)
    success_count = sum(1 for c in cases if c.run_status == "ok")
    error_count = case_count - success_count
    total_duration = sum(c.duration_seconds for c in cases)
    total_input = sum(c.input_tokens for c in cases)
    total_output = sum(c.output_tokens for c in cases)
    total_tokens = sum(c.total_tokens for c in cases)
    total_cost = sum(c.estimated_cost_usd for c in cases)

    return SummaryMetrics(
        case_count=case_count,
        success_count=success_count,
        error_count=error_count,
        total_duration_seconds=total_duration,
        avg_duration_seconds=(total_duration / case_count) if case_count else 0.0,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_tokens=total_tokens,
        total_estimated_cost_usd=total_cost,
        avg_estimated_cost_usd=(total_cost / case_count) if case_count else 0.0,
    )


def _sanitize_error_for_markdown(error: str) -> str:
    """Normalize error text for single-line markdown table rendering."""
    cleaned = error.replace("\n", " ").replace("|", "\\|").strip()
    if len(cleaned) > 140:
        return cleaned[:137] + "..."
    return cleaned


def _scope_line(cases: list[CaseMetrics]) -> str:
    """Build scope text from executed scenarios to avoid misleading hardcoded output."""
    ids = [c.scenario_id for c in cases]
    if not ids:
        return "Scope: no scenarios executed."
    return f"Scope: {', '.join(ids)}."


def render_markdown(cases: list[CaseMetrics], summary: SummaryMetrics) -> str:
    """Render a markdown benchmark report with per-case metrics and summary."""
    lines: list[str] = []
    lines.append("# OpenSRE Benchmark")
    lines.append("")
    lines.append(_scope_line(cases))
    lines.append("Metrics reported: duration, token usage, estimated LLM cost.")
    lines.append("Not measured: accuracy, false positives, false negatives.")
    lines.append("")
    lines.append("## Per-case Metrics")
    lines.append("")
    lines.append(
        "| Scenario | Status | Duration (s) | Input Tokens | Output Tokens | Total Tokens | Est. Cost (USD) | Error |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for c in cases:
        err = _sanitize_error_for_markdown(c.error) if c.error else ""
        lines.append(
            f"| {c.scenario_id} | {c.run_status} | {c.duration_seconds:.2f} | "
            f"{c.input_tokens} | {c.output_tokens} | {c.total_tokens} | "
            f"{c.estimated_cost_usd:.6f} | {err} |"
        )

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Cases: {summary.case_count}")
    lines.append(f"- Successful runs: {summary.success_count}")
    lines.append(f"- Failed runs: {summary.error_count}")
    lines.append(f"- Total duration (s): {summary.total_duration_seconds:.2f}")
    lines.append(f"- Avg duration (s): {summary.avg_duration_seconds:.2f}")
    lines.append(f"- Total input tokens: {summary.total_input_tokens}")
    lines.append(f"- Total output tokens: {summary.total_output_tokens}")
    lines.append(f"- Total tokens: {summary.total_tokens}")
    lines.append(f"- Total estimated cost (USD): {summary.total_estimated_cost_usd:.6f}")
    lines.append(f"- Avg estimated cost (USD): {summary.avg_estimated_cost_usd:.6f}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- This is an operational benchmark report, not an evaluation scorecard.")
    lines.append("- Accuracy and FP/FN require a separate evaluation workflow.")
    return "\n".join(lines) + "\n"


def run_benchmark(
    scenario_ids: list[str] | None = None,
    *,
    configure_llm: Callable[[], None] = configure_split_models,
    reasoning_usd_per_mtok: float = DEFAULT_REASONING_USD_PER_MTOK,
    tool_usd_per_mtok: float = DEFAULT_TOOL_USD_PER_MTOK,
) -> tuple[list[CaseMetrics], SummaryMetrics]:
    """Execute benchmark cases and collect duration, token, and cost metrics."""
    selected = scenario_ids if scenario_ids is not None else list(FIXED_SCENARIO_IDS)
    reasoning_model, tool_model = _resolve_models()

    cases: list[CaseMetrics] = []
    for sid in selected:
        try:
            fixture = get_fixture_by_id(sid)
            run = run_langgraph_investigation_bench(
                fixture,
                label=sid,
                configure_llm=configure_llm,
            )
            est_cost_usd, _ = estimate_run_cost_usd(
                run.tokens_by_model,
                reasoning_model=reasoning_model,
                tool_model=tool_model,
                reasoning_usd_per_mtok=reasoning_usd_per_mtok,
                tool_usd_per_mtok=tool_usd_per_mtok,
            )
            cases.append(
                CaseMetrics(
                    scenario_id=sid,
                    run_status="ok",
                    duration_seconds=run.wall_seconds,
                    input_tokens=run.tokens.input_tokens,
                    output_tokens=run.tokens.output_tokens,
                    total_tokens=run.tokens.total,
                    estimated_cost_usd=est_cost_usd,
                )
            )
        except Exception as exc:
            logger.exception("[benchmark] failed scenario %s", sid)
            cases.append(
                CaseMetrics(
                    scenario_id=sid,
                    run_status="error",
                    duration_seconds=0.0,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                    estimated_cost_usd=0.0,
                    error=str(exc),
                )
            )

    return cases, _summarize(cases)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse benchmark CLI arguments."""
    parser = argparse.ArgumentParser(description="Run OpenSRE benchmark on fixed synthetic cases.")
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Optional scenario id override (repeatable). Default: 001,002,003.",
    )
    parser.add_argument(
        "--md-out",
        default="docs/benchmarks/results.md",
        help="Path for markdown output.",
    )
    parser.add_argument(
        "--reasoning-usd-per-mtok", type=float, default=DEFAULT_REASONING_USD_PER_MTOK
    )
    parser.add_argument("--tool-usd-per-mtok", type=float, default=DEFAULT_TOOL_USD_PER_MTOK)
    parser.add_argument(
        "--no-update-readme",
        action="store_true",
        default=False,
        help="Skip updating the README.md benchmark section.",
    )
    parser.add_argument(
        "--readme-path",
        default=None,
        help="Path to README.md. Default: auto-detect repo root.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Load environment, run benchmark, and write markdown report."""
    load_dotenv(override=False)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = parse_args(argv)
    selected = list(args.scenario) if args.scenario else list(FIXED_SCENARIO_IDS)

    cases, summary = run_benchmark(
        selected,
        reasoning_usd_per_mtok=args.reasoning_usd_per_mtok,
        tool_usd_per_mtok=args.tool_usd_per_mtok,
    )

    md_out = Path(args.md_out)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(render_markdown(cases, summary), encoding="utf-8")

    logger.info("Wrote markdown report: %s", md_out)

    if not args.no_update_readme:
        from tests.benchmarks.toolcall_model_benchmark.readme_updater import (
            _find_repo_root,
            render_readme_summary,
            update_readme_benchmarks,
        )

        if args.readme_path:
            readme_path = Path(args.readme_path)
        else:
            readme_path = _find_repo_root() / "README.md"
        snippet = render_readme_summary(cases, summary)
        try:
            update_readme_benchmarks(readme_path, snippet)
        except ValueError as exc:
            logger.warning("Skipped README update: %s", exc)

    return 0 if summary.error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
