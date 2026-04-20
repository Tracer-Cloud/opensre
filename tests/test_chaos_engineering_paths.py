"""Unit tests for chaos manifest path resolution and ordering (no cluster required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.chaos_engineering.orchestrator import _datadog_values_path
from tests.chaos_engineering.paths import (
    CHAOS_ENGINEERING_DIR,
    EXPERIMENTS_DIR,
    ExperimentNotFoundError,
    experiment_chaos_yaml_paths,
    experiment_demo_yaml_paths,
    experiment_summary_line,
    infer_chaos_kind,
    list_experiment_names,
    validate_experiment,
)


def test_repo_root_contains_datadog_values() -> None:
    values = Path(_datadog_values_path())
    assert values.is_file(), f"expected {_datadog_values_path()}"
    assert values.name == "datadog-values.yaml"


def test_chaos_engineering_dir_exists() -> None:
    assert CHAOS_ENGINEERING_DIR.is_dir()
    assert (CHAOS_ENGINEERING_DIR / "experiments").is_dir()


def test_list_experiment_names_includes_crashloop() -> None:
    names = list_experiment_names()
    assert "crashloop" in names
    assert "pod-failure" in names


def test_pod_failure_yaml_ordering() -> None:
    validate_experiment("pod-failure")
    demos = experiment_demo_yaml_paths("pod-failure")
    chaos = experiment_chaos_yaml_paths("pod-failure")
    assert len(demos) == 1
    assert demos[0].name == "pod-failure-demo.yaml"
    assert len(chaos) == 1
    assert chaos[0].name == "pod-failure-chaos.yaml"


def test_crashloop_has_demo_then_chaos_glob_order() -> None:
    validate_experiment("crashloop")
    demos = experiment_demo_yaml_paths("crashloop")
    chaos = experiment_chaos_yaml_paths("crashloop")
    assert [p.name for p in demos] == ["crashloop-demo.yaml"]
    assert [p.name for p in chaos] == ["pod-kill-crashloop-chaos.yaml"]


def test_validate_experiment_missing_raises() -> None:
    with pytest.raises(ExperimentNotFoundError):
        validate_experiment("not-a-real-experiment-dir-xyz")


def test_infer_chaos_kind_first_document(tmp_path: Path) -> None:
    y = tmp_path / "x-chaos.yaml"
    y.write_text(
        "---\n"
        "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: a}\n---\n"
        "apiVersion: chaos-mesh.org/v1alpha1\nkind: NetworkChaos\nmetadata: {name: n}\n",
        encoding="utf-8",
    )
    assert infer_chaos_kind(y) == "ConfigMap"


def test_experiment_summary_line_pod_failure() -> None:
    line = experiment_summary_line("pod-failure")
    assert line.startswith("pod-failure")
    assert "PodChaos" in line


def test_experiments_dir_matches_list() -> None:
    """Every subdirectory with YAML is listed."""
    disk = sorted(p.name for p in EXPERIMENTS_DIR.iterdir() if p.is_dir() and any(p.glob("*.yaml")))
    assert list_experiment_names() == disk
