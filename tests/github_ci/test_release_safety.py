"""Workflow contracts for publishing only validated release commits."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]


def _release_workflow() -> dict[str, Any]:
    return yaml.safe_load(
        _ROOT.joinpath(".github", "workflows", "release.yml").read_text(encoding="utf-8")
    )


def _checkout(job: dict[str, Any]) -> dict[str, Any]:
    return next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v5")


def _requires_release(*paths: str) -> bool:
    result = subprocess.run(
        [sys.executable, str(_ROOT / ".github" / "ci" / "release_paths.py")],
        input="\n".join(paths),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() == "true"


def test_main_release_runs_only_after_successful_main_ci() -> None:
    workflow = _release_workflow()
    triggers = workflow[True]

    assert "push" not in triggers
    assert triggers["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }

    prepare = workflow["jobs"]["prepare"]
    assert "github.event.workflow_run.conclusion == 'success'" in prepare["if"]
    assert (
        _checkout(prepare)["with"]["ref"]
        == "${{ github.event.workflow_run.head_sha || github.sha }}"
    )
    metadata = next(
        step for step in prepare["steps"] if step.get("name") == "Resolve release metadata"
    )
    assert "git diff --name-only" in metadata["run"]
    assert '"$released_sha" "$source_sha"' in metadata["run"]
    assert '"${source_sha}^1"' not in metadata["run"]
    assert "refs/tags/main-build^{commit}" in metadata["run"]
    assert "git merge-base --is-ancestor" in metadata["run"]
    assert "git ls-tree -r --name-only" in metadata["run"]
    assert "python3 .github/ci/release_paths.py" in metadata["run"]


def test_release_builds_and_publishes_the_validated_source_sha() -> None:
    workflow = _release_workflow()
    jobs = workflow["jobs"]
    source_sha = "${{ needs.prepare.outputs.source_sha }}"

    assert jobs["prepare"]["outputs"]["source_sha"] == "${{ steps.meta.outputs.source_sha }}"

    for job_name in ("verify", "build-python-dist", "build-binaries", "publish-main-release"):
        assert _checkout(jobs[job_name])["with"]["ref"] == source_sha

    metadata = next(
        step for step in jobs["prepare"]["steps"] if step.get("name") == "Resolve release metadata"
    )
    assert 'source_sha="$WORKFLOW_RUN_HEAD_SHA"' in metadata["run"]
    assert 'echo "source_sha=${source_sha}" >> "$GITHUB_OUTPUT"' in metadata["run"]
    assert 'short_sha="${source_sha:0:7}"' in metadata["run"]

    publish = jobs["publish-main-release"]
    tag_step = next(
        step
        for step in publish["steps"]
        if step.get("name") == "Move main build tag to the latest commit"
    )
    assert 'git tag -f "${{ needs.prepare.outputs.tag_name }}" "$SOURCE_SHA"' in tag_step["run"]
    release_step = next(
        step for step in publish["steps"] if step.get("name") == "Publish rolling main release"
    )
    assert '--target "$SOURCE_SHA"' in release_step["run"]
    assert publish["steps"].index(tag_step) > publish["steps"].index(release_step)


def test_release_path_classifier_preserves_the_previous_push_filters() -> None:
    assert not _requires_release(
        "docs/quickstart.mdx",
        "tests/cli/test_smoke.py",
        "README.md",
        ".claude/settings.json",
        "infrastructure/deployment/cloudflare_install_proxy/worker.py",
    )
    assert _requires_release("core/agent_harness/turns/driver.py")
    assert _requires_release("pyproject.toml")
