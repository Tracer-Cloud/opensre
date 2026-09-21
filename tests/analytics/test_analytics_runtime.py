from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.analytics.analytics_runtime import (
    detect_analytics_runtime,
    detect_container_runtime,
    is_ci_environment,
)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CI", "true"),
        ("CI", "on"),
        ("GITHUB_ACTIONS", "1"),
        ("GITLAB_CI", "yes"),
        ("JENKINS_URL", "https://jenkins.example"),
        ("BITBUCKET_BUILD_NUMBER", "42"),
    ],
)
def test_is_ci_environment_recognizes_generic_and_vendor_signals(
    key: str,
    value: str,
) -> None:
    assert is_ci_environment({key: value}) is True


def test_is_ci_environment_rejects_false_and_empty_values() -> None:
    assert is_ci_environment({"CI": "false", "JENKINS_URL": ""}) is False


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"KUBERNETES_SERVICE_HOST": "10.0.0.1"}, "kubernetes"),
        ({"ECS_CONTAINER_METADATA_URI_V4": "http://169.254.170.2"}, "ecs"),
        ({"container": "podman"}, "podman"),
        ({"CONTAINER": "unknown-runtime"}, "container"),
    ],
)
def test_detect_container_runtime_uses_environment_signals(
    tmp_path: Path,
    environment: dict[str, str],
    expected: str,
) -> None:
    assert detect_container_runtime(environment, tmp_path) == expected


def test_detect_container_runtime_uses_docker_marker(tmp_path: Path) -> None:
    (tmp_path / ".dockerenv").touch()

    assert detect_container_runtime({}, tmp_path) == "docker"


def test_detect_container_runtime_uses_cgroup_marker(tmp_path: Path) -> None:
    cgroup_path = tmp_path / "proc/1/cgroup"
    cgroup_path.parent.mkdir(parents=True)
    cgroup_path.write_text("0::/kubepods/burstable/pod123", encoding="utf-8")

    assert detect_container_runtime({}, tmp_path) == "kubernetes"


def test_detect_container_runtime_returns_none_without_signals(tmp_path: Path) -> None:
    assert detect_container_runtime({}, tmp_path) is None


@pytest.mark.parametrize(
    ("environment", "with_docker", "expected_environment", "is_ci", "is_container"),
    [
        ({}, False, "local", False, False),
        ({"CI": "true"}, False, "ci", True, False),
        ({}, True, "container", False, True),
        ({"CI": "true"}, True, "ci_container", True, True),
    ],
)
def test_detect_analytics_runtime_builds_filterable_dimensions(
    tmp_path: Path,
    environment: dict[str, str],
    with_docker: bool,
    expected_environment: str,
    is_ci: bool,
    is_container: bool,
) -> None:
    if with_docker:
        (tmp_path / ".dockerenv").touch()
    for process in ("1", "self"):
        path = tmp_path / "proc" / process / "cgroup"
        path.parent.mkdir(parents=True)
        path.write_text("0::/", encoding="utf-8")

    context = detect_analytics_runtime(environment, tmp_path)

    assert context.execution_environment == expected_environment
    assert context.is_ci is is_ci
    assert context.is_container is is_container
    assert context.container_runtime == ("docker" if with_docker else "none")


def test_unavailable_linux_probes_are_unknown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")
    context = detect_analytics_runtime({}, tmp_path)
    assert context.is_container is None
    assert context.container_detection_status == "unknown"
    assert context.execution_environment == "unknown"


def test_container_availability_uses_the_same_observation(monkeypatch, tmp_path: Path) -> None:
    observations = iter((("", False), ("0::/docker/123", True)))

    def _read_cgroup(_root: Path) -> tuple[str, bool]:
        return next(observations)

    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("infrastructure.analytics.analytics_runtime._read_cgroup", _read_cgroup)

    context = detect_analytics_runtime({}, tmp_path)

    assert context.is_container is None
    assert context.container_detection_status == "unknown"
