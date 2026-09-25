"""Classify the runtime that emits product analytics events."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from config.constants.analytics import ANALYTICS_CICD_ENV

_TRUTHY_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes"})
_CI_BOOLEAN_ENV_KEYS: Final[tuple[str, ...]] = (
    ANALYTICS_CICD_ENV,
    "CI",
    "CONTINUOUS_INTEGRATION",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "CIRCLECI",
    "BUILDKITE",
    "TRAVIS",
    "TF_BUILD",
)
_CI_PRESENCE_ENV_KEYS: Final[tuple[str, ...]] = (
    "JENKINS_URL",
    "TEAMCITY_VERSION",
    "BITBUCKET_BUILD_NUMBER",
)
_CONTAINER_ENV_VALUES: Final[dict[str, str]] = {
    "docker": "docker",
    "podman": "podman",
    "containerd": "containerd",
    "lxc": "lxc",
}
_CGROUP_RUNTIME_MARKERS: Final[tuple[tuple[str, str], ...]] = (
    ("kubepods", "kubernetes"),
    ("ecs", "ecs"),
    ("libpod", "podman"),
    ("podman", "podman"),
    ("docker", "docker"),
    ("containerd", "containerd"),
    ("lxc", "lxc"),
)


@dataclass(frozen=True, slots=True)
class AnalyticsRuntime:
    """Stable, queryable classification for an analytics-emitting process."""

    execution_environment: str
    is_ci: bool
    is_container: bool
    container_runtime: str


def _normalized_env(environ: Mapping[str, str], key: str) -> str:
    return environ.get(key, "").strip().lower()


def has_cicd_marker(environ: Mapping[str, str] | None = None) -> bool:
    """Return the explicit pipeline marker, independently of vendor detection."""
    values = os.environ if environ is None else environ
    return _normalized_env(values, ANALYTICS_CICD_ENV) in _TRUTHY_VALUES


def is_ci_environment(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether standard CI/CD environment signals are present."""
    values = os.environ if environ is None else environ
    if any(_normalized_env(values, key) in _TRUTHY_VALUES for key in _CI_BOOLEAN_ENV_KEYS):
        return True
    return any(_normalized_env(values, key) for key in _CI_PRESENCE_ENV_KEYS)


def _read_cgroup(filesystem_root: Path) -> str:
    contents: list[str] = []
    for relative_path in ("proc/1/cgroup", "proc/self/cgroup"):
        try:
            contents.append(
                (filesystem_root / relative_path).read_text(encoding="utf-8", errors="ignore")
            )
        except OSError:
            continue
    return "\n".join(contents).lower()


def detect_container_runtime(
    environ: Mapping[str, str] | None = None,
    filesystem_root: Path | None = None,
) -> str | None:
    """Return the detected container runtime, if the process is containerized."""
    values = os.environ if environ is None else environ
    root = Path("/") if filesystem_root is None else filesystem_root

    if _normalized_env(values, "KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    if _normalized_env(values, "ECS_CONTAINER_METADATA_URI_V4") or _normalized_env(
        values, "ECS_CONTAINER_METADATA_URI"
    ):
        return "ecs"

    declared_runtime = _normalized_env(values, "container") or _normalized_env(values, "CONTAINER")
    if declared_runtime:
        return _CONTAINER_ENV_VALUES.get(declared_runtime, "container")
    if (root / ".dockerenv").exists():
        return "docker"
    if (root / "run/.containerenv").exists():
        return "podman"

    cgroup = _read_cgroup(root)
    for marker, runtime in _CGROUP_RUNTIME_MARKERS:
        if marker in cgroup:
            return runtime
    return None


def detect_analytics_runtime(
    environ: Mapping[str, str] | None = None,
    filesystem_root: Path | None = None,
) -> AnalyticsRuntime:
    """Return first-party traffic dimensions for analytics filtering."""
    is_ci = is_ci_environment(environ)
    container_runtime = detect_container_runtime(environ, filesystem_root)
    is_container = container_runtime is not None

    if is_ci and is_container:
        execution_environment = "ci_container"
    elif is_ci:
        execution_environment = "ci"
    elif is_container:
        execution_environment = "container"
    else:
        execution_environment = "local"

    return AnalyticsRuntime(
        execution_environment=execution_environment,
        is_ci=is_ci,
        is_container=is_container,
        container_runtime=container_runtime or "none",
    )
