"""Guards against CI collecting zero tests under pytest-xdist.

Two failure modes have produced ``N workers [0 items]`` in CI:

1. A mangled ``PYTEST_MARKER_EXPR`` (e.g. boolean ``false``) deselects everything.
   ``tests/conftest.py`` forces exit code 5 in that case.

2. A missing path argument (file/dir deleted but still listed in
   ``.github/workflows/ci.yml``) makes xdist abort collection for the *whole*
   shard — even when other paths are valid. Seen after ``tests/github`` was
   removed while ``cli-runtime`` still referenced it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PATH_RE = re.compile(r"^(tests/\S+|gateway/tests)$")
_IGNORE_RE = re.compile(r"--ignore=(\S+)")

# Test directories deliberately outside pull-request CI. Prefixes: an entry also
# excuses everything beneath it.
_NOT_IN_PR_CI = (
    # Runs in synthetic-deterministic.yml.
    "tests/synthetic",
    # Opt-in live: these hit the CDN, Homebrew, and a real LLM.
    "tests/e2e/install",
    "tests/e2e/quickstart",
    "tests/e2e/kubernetes_local_alert_simulation",
    # Deprecated live AWS demo; no longer part of CI.
    "tests/e2e/upstream_prefect_ecs_fargate",
)


@dataclass(frozen=True, slots=True)
class _Shard:
    """One CI matrix entry, as the paths it runs and the paths it skips."""

    name: str
    claims: frozenset[str]
    ignores: frozenset[str]


def _covers(paths: frozenset[str], directory: str) -> bool:
    """Whether ``paths`` holds ``directory`` itself or one of its ancestors."""
    return any(directory == p or directory.startswith(p.rstrip("/") + "/") for p in paths)


def _shards() -> list[_Shard]:
    """Return the CI test matrix, with each shard's ignores folded in.

    ``--ignore`` arrives from two places: the shared ``Run tests`` step, which
    applies to every shard, and a shard's own ``extra_pytest_args``.
    """
    workflow: dict[str, Any] = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    test_job = workflow["jobs"]["test"]
    run_step = next(s for s in test_job["steps"] if "pytest" in str(s.get("run", "")))
    shared_ignores = frozenset(_IGNORE_RE.findall(str(run_step["run"])))

    return [
        _Shard(
            name=str(entry.get("shard", "?")),
            claims=frozenset(
                token
                for token in str(entry.get("pytest_paths") or "").split()
                if _PATH_RE.match(token)
            ),
            ignores=frozenset(_IGNORE_RE.findall(str(entry.get("extra_pytest_args") or "")))
            | shared_ignores,
        )
        for entry in test_job["strategy"]["matrix"]["include"]
    ]


def _shard_pytest_paths() -> list[tuple[str, str]]:
    """Return ``(shard, path)`` for every path token the CI matrix names."""
    return [(shard.name, token) for shard in _shards() for token in sorted(shard.claims)]


def test_xdist_empty_marker_exits_no_tests_collected() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-n",
            "2",
            "-q",
            "tests/packaging",
            "-m",
            "false",
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "0 items" in result.stdout or "0 items" in result.stderr
    assert result.returncode == 5, (
        f"expected ExitCode.NO_TESTS_COLLECTED (5), got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_ci_pytest_paths_exist_in_git_tree() -> None:
    """Every ``matrix.pytest_paths`` entry must exist in the committed tree.

    Local empty leftover dirs (e.g. ``tests/github/`` with only ``__pycache__``)
    hide this; CI checkouts do not have them, and a missing path zeros xdist.
    """
    tracked = set(
        subprocess.check_output(
            ["git", "-C", str(_REPO_ROOT), "ls-tree", "-r", "--name-only", "HEAD"],
            text=True,
        ).splitlines()
    )

    def _present(path: str) -> bool:
        if path in tracked:
            return True
        prefix = path.rstrip("/") + "/"
        return any(entry.startswith(prefix) for entry in tracked)

    missing = [f"{shard}: {token}" for shard, token in _shard_pytest_paths() if not _present(token)]

    assert not missing, (
        "CI pytest_paths missing from git tree (xdist will collect 0 items):\n"
        + "\n".join(f"  - {item}" for item in missing)
    )


def test_every_test_directory_runs_in_a_shard() -> None:
    """The reverse of the check above: every test directory must reach a shard.

    ``pytest_paths`` is a hand-maintained list, so a new directory runs nowhere
    until someone remembers to add it, and nothing fails when they don't — the
    tests simply never execute. ``tests/bootstrap`` sat unsharded that way until
    #5240, and ``tests/filestorage``, ``tests/surfaces`` and ``tests/quality``
    until #5349, between them 304 tests that no pull request ran.
    """
    shards = _shards()
    # Every depth, not just the immediate children of ``tests/``: a shard can
    # claim a parent and ``--ignore`` a directory beneath it, which leaves that
    # directory running nowhere unless another shard picks it up.
    directories = sorted(
        {
            path.parent.relative_to(_REPO_ROOT).as_posix()
            for path in _REPO_ROOT.joinpath("tests").rglob("test_*.py")
        }
    )

    uncovered = [
        directory
        for directory in directories
        if not _covers(frozenset(_NOT_IN_PR_CI), directory)
        and not any(
            _covers(shard.claims, directory) and not _covers(shard.ignores, directory)
            for shard in shards
        )
    ]

    assert not uncovered, (
        "test directories in no CI shard (their tests never run on a PR):\n"
        + "\n".join(f"  - {item}" for item in uncovered)
        + "\nAdd each to a shard's pytest_paths in .github/workflows/ci.yml."
    )


def test_live_tool_selection_runs_only_on_openai_live_shard() -> None:
    """Keep selection tests out of broad shards and in the required live gate."""
    claimed = [shard for shard in _shards() if _covers(shard.claims, "tests/tools/selection")]
    running = [shard for shard in claimed if not _covers(shard.ignores, "tests/tools/selection")]

    assert [shard.name for shard in running] == ["cli-live-agent"]
    assert all(
        "tests/tools/selection" in shard.ignores
        for shard in claimed
        if shard.name.startswith("tools-runtime-")
    )
