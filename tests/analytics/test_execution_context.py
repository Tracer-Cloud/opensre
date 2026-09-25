"""Launch-boundary and token-isolation regressions for runner telemetry."""

from __future__ import annotations

import json
from pathlib import Path

from infrastructure.analytics.runner_launcher import docker_command
from infrastructure.analytics.runner_provenance import execution_evidence, read_runner_provenance


def test_context_reading_is_bounded_and_malformed_evidence_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "context.json"
    env = {"OPENSRE_EXECUTION_CONTEXT_PATH": str(path)}
    for raw in ("{", "[]", " " * 24_577, '{"version":1,"execution_origin":"github_actions"}'):
        path.write_text(raw)
        assert read_runner_provenance(env) is None


def test_context_token_is_bound_to_runtime_identity(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "context.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "analytics_id": "expected",
                "execution_origin": "github_actions",
                "token": "private-token",
            }
        )
    )
    monkeypatch.setenv("OPENSRE_EXECUTION_CONTEXT_PATH", str(path))
    props, headers = execution_evidence("other", is_ci=False, is_container=True)
    assert props["automation_status"] == "unknown"
    assert headers == {}
    props, headers = execution_evidence("expected", is_ci=False, is_container=True)
    assert props["is_ci"] is True
    assert props["ci_detection_status"] == "detected"
    assert headers["X-OpenSRE-Runner-Token"] == "private-token"
    assert "private-token" not in repr(read_runner_provenance())
    assert "private-token" not in json.dumps(props)


def test_docker_launch_mounts_context_without_relying_on_ci_environment(tmp_path: Path) -> None:
    command = docker_command("runtime-image", tmp_path, ["opensre", "--record-install"])
    assert f"type=bind,source={tmp_path},target=/run/opensre,readonly" in command
    assert "OPENSRE_WIZARD_STORE_PATH=/opensre-home/opensre.json" in command
    assert "CI" not in command and "GITHUB_ACTIONS" not in command
    assert command[-3:] == ["runtime-image", "opensre", "--record-install"]
