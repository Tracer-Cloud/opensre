"""Workflow contracts for the ninety-second pull-request execution gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load(_ROOT.joinpath(".github", "workflows", name).read_text(encoding="utf-8"))


def _action(name: str) -> dict[str, Any]:
    return yaml.safe_load(
        _ROOT.joinpath(".github", "actions", name, "action.yml").read_text(encoding="utf-8")
    )


def test_full_codeql_is_post_merge_and_pr_profile_is_manual() -> None:
    workflow = _workflow("codeql.yml")
    triggers = workflow[True]

    assert "pull_request" not in triggers
    assert {"push", "schedule", "workflow_dispatch"} <= set(triggers)

    jobs = workflow["jobs"]
    full = jobs["analyze-full"]
    assert full["strategy"]["matrix"]["language"] == [
        "python",
        "javascript-typescript",
    ]
    full_init = next(step for step in full["steps"] if step.get("name") == "Initialize CodeQL")
    assert full_init["with"]["queries"] == "security-and-quality"
    full_analyze = next(
        step for step in full["steps"] if step.get("name") == "Perform CodeQL Analysis"
    )
    assert full_analyze["with"]["category"] == "/language:${{ matrix.language }}"

    benchmark = jobs["analyze-pr-benchmark"]
    benchmark_init = next(
        step for step in benchmark["steps"] if step.get("name") == "Initialize CodeQL"
    )
    assert benchmark_init["with"]["config-file"] == ".github/codeql/codeql-pr-config.yml"
    assert "queries" not in benchmark_init["with"]
    benchmark_analyze = next(
        step for step in benchmark["steps"] if step.get("name") == "Perform CodeQL Analysis"
    )
    assert benchmark_analyze["with"]["category"] == "/language:python/pr-fast"


def test_heavy_test_suites_are_duration_balanced_with_measured_headroom() -> None:
    test_job = _workflow("ci.yml")["jobs"]["test"]
    entries = test_job["strategy"]["matrix"]["include"]

    assert test_job["strategy"]["max-parallel"] == 23
    expected_splits = {
        "tools-runtime": 6,
        "cli-runtime": 6,
        "integrations-and-misc": 6,
    }
    for base, splits in expected_splits.items():
        groups = [entry for entry in entries if entry["shard"].startswith(f"{base}-")]
        assert [entry["shard"] for entry in groups] == [
            f"{base}-{group}" for group in range(1, splits + 1)
        ]
        assert all(f"--splits={splits}" in entry["shard_args"] for entry in groups)

    live_agent = next(entry for entry in entries if entry["shard"] == "cli-live-agent")
    assert live_agent["llm_provider"] == "openai"
    assert live_agent["pytest_paths"].split() == [
        "tests/tools/selection",
    ]
    tool_groups = [entry for entry in entries if entry["shard"].startswith("tools-runtime-")]
    assert all(
        "--ignore=tests/tools/selection" in entry["extra_pytest_args"] for entry in tool_groups
    )
    cli_groups = [entry for entry in entries if entry["shard"].startswith("cli-runtime-")]
    assert all(
        "--ignore=tests/cli/test_smoke.py" in entry["extra_pytest_args"] for entry in cli_groups
    )

    smoke_groups = {
        entry["shard"]: entry for entry in entries if entry["shard"].startswith("cli-smoke-")
    }
    assert set(smoke_groups) == {"cli-smoke-1", "cli-smoke-2"}
    assert all(
        entry["pytest_paths"] == "tests/cli/test_smoke.py" for entry in smoke_groups.values()
    )
    first_selector = smoke_groups["cli-smoke-1"]["extra_pytest_args"].removeprefix("-k ")
    second_selector = smoke_groups["cli-smoke-2"]["extra_pytest_args"].removeprefix("-k ")
    first_expression = first_selector.removeprefix("'").removesuffix("'")
    assert second_selector == "'not (" + first_expression + ")'"

    run_step = next(step for step in test_job["steps"] if step.get("name") == "Run tests")
    assert "-p tests.ci_sharding" in run_step["run"]
    assert "steps.shard.outputs.pytest_paths || matrix.pytest_paths" in run_step["run"]
    assert "--ci-durations-output=" in run_step["run"]
    assert "--cov=config" not in run_step["run"]
    assert "github.event_name == 'push'" in run_step["env"]["PYTEST_COVERAGE_ARGS"]

    prepartition = next(
        step for step in test_job["steps"] if step.get("name") == "Pre-partition pytest files"
    )
    assert "tests.ci_sharding select" in prepartition["run"]
    assert "matrix.shard_args" in prepartition["if"]
    assert "matrix.pytest_paths" in prepartition["run"]


def test_main_builds_a_reviewable_timing_snapshot_artifact() -> None:
    jobs = _workflow("ci.yml")["jobs"]
    test_steps = jobs["test"]["steps"]
    timing_upload = next(
        step for step in test_steps if step.get("name") == "Upload shard timing data"
    )
    assert "github.event_name == 'push'" in timing_upload["if"]
    assert timing_upload["with"]["name"] == "pytest-timings-${{ matrix.shard }}"

    coverage_steps = jobs["coverage-report"]["steps"]
    merge = next(
        step for step in coverage_steps if step.get("name") == "Merge pytest timing snapshot"
    )
    assert "tests.ci_sharding merge" in merge["run"]
    snapshot = next(
        step
        for step in coverage_steps
        if step.get("name") == "Upload reviewable pytest timing snapshot"
    )
    assert snapshot["with"]["path"] == ".pytest-timings/pytest-file-durations.json"


def test_quality_jobs_start_in_parallel_and_gate_aggregates_them() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]

    assert workflow["permissions"]["pull-requests"] == "read"
    assert "changes" not in jobs
    assert "needs" not in jobs["quality-static"]
    assert "needs" not in jobs["quality-typecheck"]
    assert "needs" not in jobs["test"]
    assert "needs" not in jobs["session-store-locked"]
    assert "needs" not in jobs["package-preflight"]
    assert "Restore mypy cache" in {step.get("name") for step in jobs["quality-typecheck"]["steps"]}
    assert "Verify typed tool contracts" in {
        step.get("name") for step in jobs["quality-typecheck"]["steps"]
    }
    assert "Verify tool registry index" in {
        step.get("name") for step in jobs["quality-static"]["steps"]
    }
    tool_groups = [
        entry
        for entry in jobs["test"]["strategy"]["matrix"]["include"]
        if entry["shard"].startswith("tools-runtime-")
    ]
    assert all(
        "--ignore=tests/core/tool/test_contracts.py" in entry["extra_pytest_args"]
        for entry in tool_groups
    )
    assert all(
        "--ignore=tests/tools/test_registry_index.py" in entry["extra_pytest_args"]
        for entry in tool_groups
    )
    assert set(jobs["ci-gate"]["needs"]) == {
        "quality-static",
        "quality-typecheck",
        "test",
        "coverage-report",
        "session-store-locked",
        "package-preflight",
    }


def test_package_preflight_builds_and_smokes_changed_distribution_artifacts() -> None:
    workflow = _workflow("ci.yml")
    job = workflow["jobs"]["package-preflight"]

    changes = next(step for step in job["steps"] if step.get("id") == "changes")
    filters = yaml.safe_load(changes["with"]["filters"])
    assert {
        "README.md",
        "LICENSE",
        "MANIFEST.in",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "uv.lock",
        "surfaces/**",
        "tests/**",
        "tools/**",
    } <= set(filters["packaging"])

    install_uv = next(step for step in job["steps"] if step.get("name") == "Install uv")
    assert install_uv["if"] == "steps.changes.outputs.packaging == 'true'"
    setup_python = next(step for step in job["steps"] if step.get("name") == "Set up Python")
    assert setup_python["if"] == "steps.changes.outputs.packaging == 'true'"

    build = next(
        step for step in job["steps"] if step.get("name") == "Build and validate distributions"
    )
    assert build["if"] == "steps.changes.outputs.packaging == 'true'"
    assert "python -m build --outdir dist" in build["run"]
    assert "twine check dist/*" in build["run"]
    assert "validate_wheel.py dist/*.whl" in build["run"]

    smoke = next(step for step in job["steps"] if step.get("name") == "Smoke the installed wheel")
    assert smoke["if"] == "steps.changes.outputs.packaging == 'true'"
    assert "uv pip install --python" in smoke["run"]
    assert '"$smoke_env/bin/opensre" --version' in smoke["run"]
    assert '"$smoke_env/bin/opensre" _package-smoke' in smoke["run"]

    gate_run = next(
        step["run"]
        for step in workflow["jobs"]["ci-gate"]["steps"]
        if step.get("name") == "Require green upstream jobs"
    )
    assert "package_preflight='${{ needs.package-preflight.result }}'" in gate_run
    assert '[ "$package_preflight" = success ]' in gate_run


def test_source_filter_defaults_to_running_ci_for_new_file_types() -> None:
    inputs = _action("detect-source")["runs"]["steps"][0]["with"]
    filters = yaml.safe_load(inputs["filters"])["source"]

    assert inputs["predicate-quantifier"] == "every"
    assert filters == ["**", "!**/*.md", "!**/*.mdx", "!docs/**"]


def test_session_store_locked_job_contracts() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]
    locked_job = jobs["session-store-locked"]

    assert (
        locked_job["outputs"]["session_persistence"]
        == "${{ steps.changes.outputs.session_persistence }}"
    )
    change_step = next(step for step in locked_job["steps"] if step.get("id") == "changes")
    filters = yaml.safe_load(change_step["with"]["filters"])
    assert filters["session_persistence"] == ["core/agent_harness/session/persistence/**"]
    # Without every, the bare "**" in `source` always matches and the
    # negations (!*.md etc.) never take effect — see detect-source/action.yml.
    assert change_step["with"]["predicate-quantifier"] == "every"

    gate_run = next(
        step["run"]
        for step in jobs["ci-gate"]["steps"]
        if step.get("name") == "Require green upstream jobs"
    )
    assert (
        "session_persistence_changed='${{ needs.session-store-locked.outputs.session_persistence }}'"
        in gate_run
    )
    assert 'if [ "$session_persistence_changed" = "true" ]; then' in gate_run
