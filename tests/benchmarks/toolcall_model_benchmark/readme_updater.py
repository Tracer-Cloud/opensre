"""Inject benchmark summary into README.md between HTML comment markers.

Follows the same marker-replacement pattern used by the contributors workflow
(.github/workflows/contributors.yml): content between a start and end marker
is replaced idempotently via ``re.sub`` with ``re.DOTALL``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_START_MARKER = "<!-- BENCHMARK-START -->"


@dataclass(frozen=True)
class _CaseMetrics:
    """Lightweight case data parsed from a benchmark report."""

    scenario_id: str
    run_status: str
    duration_seconds: float
    total_tokens: int
    estimated_cost_usd: float


@dataclass(frozen=True)
class _SummaryMetrics:
    """Lightweight summary data parsed from a benchmark report."""

    case_count: int
    success_count: int
    total_duration_seconds: float
    total_estimated_cost_usd: float
DEFAULT_END_MARKER = "<!-- BENCHMARK-END -->"


def update_readme_benchmarks(
    readme_path: Path,
    snippet: str,
    *,
    start_marker: str = DEFAULT_START_MARKER,
    end_marker: str = DEFAULT_END_MARKER,
) -> None:
    """Replace the section between *start_marker* and *end_marker* in *readme_path*.

    Raises ``ValueError`` if either marker is missing from the file.
    """
    content = readme_path.read_text(encoding="utf-8")

    if start_marker not in content:
        raise ValueError(f"Start marker {start_marker!r} not found in {readme_path}")
    if end_marker not in content:
        raise ValueError(f"End marker {end_marker!r} not found in {readme_path}")

    pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
    replacement = f"{start_marker}\n{snippet}\n{end_marker}"
    updated = re.sub(pattern, replacement, content, flags=re.DOTALL)

    readme_path.write_text(updated, encoding="utf-8")
    logger.info("Updated benchmark section in %s", readme_path)


def _find_repo_root() -> Path:
    """Walk up from this file to find the repository root (contains README.md)."""
    current = Path(__file__).resolve().parent
    for parent in (current, *current.parents):
        if (parent / "README.md").exists():
            return parent
    raise FileNotFoundError("Could not locate repository root with README.md")


def main() -> int:
    """Update README benchmark section from the cached results report.

    Parses ``docs/benchmarks/results.md`` to extract the per-case table and
    summary, then injects a compact snippet into README.md.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    repo_root = _find_repo_root()
    results_path = repo_root / "docs" / "benchmarks" / "results.md"
    readme_path = repo_root / "README.md"

    if not results_path.exists():
        logger.error("No benchmark results found at %s. Run 'make benchmark' first.", results_path)
        return 1

    snippet = extract_summary_from_report(results_path.read_text(encoding="utf-8"))
    update_readme_benchmarks(readme_path, snippet)
    return 0


def _parse_report_to_metrics(
    report: str,
) -> tuple[list[_CaseMetrics], _SummaryMetrics] | None:
    """Parse a benchmark report back into structured metrics.

    Returns ``None`` if the report cannot be parsed (empty or malformed).
    """
    cases: list[_CaseMetrics] = []
    summary_kv: dict[str, str] = {}

    # Parse per-case table rows
    in_table = False
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if line.startswith("| Scenario"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 7:
                cases.append(_CaseMetrics(
                    scenario_id=cols[0],
                    run_status=cols[1],
                    duration_seconds=float(cols[2]),
                    total_tokens=int(cols[5]),
                    estimated_cost_usd=float(cols[6]),
                ))

    # Parse summary section
    in_summary = False
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if line == "## Summary":
            in_summary = True
            continue
        if in_summary:
            if line.startswith("##"):
                break
            if line.startswith("- "):
                key, _, val = line[2:].partition(":")
                summary_kv[key.strip()] = val.strip()

    if not cases:
        return None

    case_count = int(summary_kv.get("Cases", str(len(cases))))
    success_count = int(summary_kv.get("Successful runs", str(sum(1 for c in cases if c.run_status == "ok"))))
    total_duration = float(summary_kv.get("Total duration (s)", str(sum(c.duration_seconds for c in cases))))
    total_cost = float(summary_kv.get("Total estimated cost (USD)", str(sum(c.estimated_cost_usd for c in cases))))

    summary = _SummaryMetrics(
        case_count=case_count,
        success_count=success_count,
        total_duration_seconds=total_duration,
        total_estimated_cost_usd=total_cost,
    )
    return cases, summary


def extract_summary_from_report(report: str) -> str:
    """Extract a compact summary snippet from a full benchmark report.

    Parses the report back into structured metrics and renders using the same
    ``render_readme_summary`` function used by ``make benchmark``, ensuring
    both update paths produce identical README content.
    """
    from tests.benchmarks.toolcall_model_benchmark.benchmark_generator import (
        CaseMetrics,
        SummaryMetrics,
        render_readme_summary,
    )

    parsed = _parse_report_to_metrics(report)
    if parsed is None:
        return "\nFull report: [docs/benchmarks/results.md](docs/benchmarks/results.md)"

    raw_cases, raw_summary = parsed

    cases = [
        CaseMetrics(
            scenario_id=c.scenario_id,
            run_status=c.run_status,
            duration_seconds=c.duration_seconds,
            input_tokens=0,
            output_tokens=0,
            total_tokens=c.total_tokens,
            estimated_cost_usd=c.estimated_cost_usd,
        )
        for c in raw_cases
    ]
    summary = SummaryMetrics(
        case_count=raw_summary.case_count,
        success_count=raw_summary.success_count,
        error_count=raw_summary.case_count - raw_summary.success_count,
        total_duration_seconds=raw_summary.total_duration_seconds,
        avg_duration_seconds=(
            raw_summary.total_duration_seconds / raw_summary.case_count
            if raw_summary.case_count else 0.0
        ),
        total_input_tokens=0,
        total_output_tokens=0,
        total_tokens=sum(c.total_tokens for c in raw_cases),
        total_estimated_cost_usd=raw_summary.total_estimated_cost_usd,
        avg_estimated_cost_usd=(
            raw_summary.total_estimated_cost_usd / raw_summary.case_count
            if raw_summary.case_count else 0.0
        ),
    )
    return render_readme_summary(cases, summary)


if __name__ == "__main__":
    raise SystemExit(main())
