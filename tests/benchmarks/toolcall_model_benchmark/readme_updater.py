"""Inject benchmark summary into README.md between HTML comment markers.

Follows the same marker-replacement pattern used by the contributors workflow
(.github/workflows/contributors.yml): content between a start and end marker
is replaced idempotently via ``re.sub`` with ``re.DOTALL``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_START_MARKER = "<!-- BENCHMARK-START -->"
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


def extract_summary_from_report(report: str) -> str:
    """Extract a compact summary snippet from a full benchmark report.

    Pulls the per-case table (simplified) and the summary bullet list, then
    appends a link to the full report.
    """
    lines: list[str] = []

    in_table = False
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if line.startswith("| Scenario"):
            in_table = True
        if in_table:
            if line.startswith("|"):
                lines.append(line)
            else:
                break

    summary_lines: list[str] = []
    in_summary = False
    for raw_line in report.splitlines():
        line = raw_line.strip()
        if line == "## Summary":
            in_summary = True
            continue
        if in_summary:
            if line.startswith("##") or (not line and summary_lines and not summary_lines[-1]):
                break
            if line.startswith("- "):
                summary_lines.append(line)

    result_parts: list[str] = []
    if lines:
        result_parts.append("\n".join(lines))
    if summary_lines:
        result_parts.append("\n".join(summary_lines))
    result_parts.append("\nFull report: [docs/benchmarks/results.md](docs/benchmarks/results.md)")

    return "\n\n".join(result_parts)


if __name__ == "__main__":
    raise SystemExit(main())
