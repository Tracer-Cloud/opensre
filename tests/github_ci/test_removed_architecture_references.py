from __future__ import annotations

from pathlib import Path

from config.constants.paths import REPO_ROOT
from integrations.github.tools.ci_analytics.benchmarks import BENCHMARKS
from tests.utils.tracked_sources import tracked_files, tracked_python_files

ROOT = REPO_ROOT
# The CI analytics report compares against well-known public repositories by
# their ``owner/repo`` slug. Naming one as a benchmark peer is data, not a
# return to the framework; only the exact slug is tolerated.
BENCHMARK_REPOSITORY_SLUGS = tuple(benchmark.label.lower() for benchmark in BENCHMARKS)
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-devcontainer",
    "__pycache__",
    "build",
    "htmlcov",
    "node_modules",
    "opensre.egg-info",
    "plans",
    "tasks",
}
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".mdc",
    ".mdx",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _iter_repo_text_files() -> list[Path]:
    files: list[Path] = []
    for path in tracked_files(str(ROOT)):
        parts = path.parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        if "site-packages" in parts:
            continue
        if not path.is_file():
            continue
        if path.name in {"Dockerfile", "Makefile"} or path.suffix in TEXT_SUFFIXES:
            files.append(path)
    return files


def test_removed_framework_names_do_not_reappear() -> None:
    removed = ("lang" + "graph", "lang" + "chain", "lang" + "smith")
    offenders: list[str] = []

    for path in _iter_repo_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for slug in BENCHMARK_REPOSITORY_SLUGS:
            text = text.replace(slug, "")
        if any(token in text for token in removed):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_deleted_app_nodes_package_is_not_referenced_by_python_code() -> None:
    deleted_package = "app." + "nodes"
    offenders: list[str] = []

    for path in tracked_python_files(str(ROOT)):
        if not path.exists():
            continue
        rel = path.relative_to(ROOT)
        if not rel.parts or rel.parts[0] not in {"app", "tests"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if deleted_package in text:
            offenders.append(str(rel))

    assert offenders == []
