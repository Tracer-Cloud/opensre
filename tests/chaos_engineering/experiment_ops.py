"""Apply and delete per-experiment manifests in convention order."""

from __future__ import annotations

from tests.chaos_engineering import kubectl
from tests.chaos_engineering.paths import (
    experiment_chaos_yaml_paths,
    experiment_demo_yaml_paths,
    validate_experiment,
)


def apply_experiment(name: str, *, context: str | None) -> None:
    validate_experiment(name)
    for path in experiment_demo_yaml_paths(name):
        kubectl.kubectl_apply(path, context=context)
    for path in experiment_chaos_yaml_paths(name):
        kubectl.kubectl_apply(path, context=context)


def delete_experiment(name: str, *, context: str | None) -> None:
    validate_experiment(name)
    for path in reversed(experiment_chaos_yaml_paths(name)):
        kubectl.kubectl_delete(path, context=context)
    for path in reversed(experiment_demo_yaml_paths(name)):
        kubectl.kubectl_delete(path, context=context)
