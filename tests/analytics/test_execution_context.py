"""Launch-boundary and token-isolation regressions for runner telemetry."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from config.constants.analytics import ANALYTICS_RUNNER_INGEST_URL, ANALYTICS_RUNNER_TOKEN_HEADER
from infrastructure.analytics import runner_launcher
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
    props, headers = execution_evidence(
        "other", endpoint_url=ANALYTICS_RUNNER_INGEST_URL, is_ci=False, is_container=True
    )
    assert props["automation_status"] == "unknown"
    assert headers == {}
    props, headers = execution_evidence(
        "expected", endpoint_url=ANALYTICS_RUNNER_INGEST_URL, is_ci=False, is_container=True
    )
    assert props["is_ci"] is True
    assert props["ci_detection_status"] == "detected"
    assert headers[ANALYTICS_RUNNER_TOKEN_HEADER] == "private-token"
    assert "private-token" not in repr(read_runner_provenance())
    assert "private-token" not in json.dumps(props)
    for destination in (
        "https://custom.invalid/api/analytics/events",
        "http://app.opensre.com/api/analytics/events",
        "https://app.opensre.com.invalid/api/analytics/events",
    ):
        _, custom_headers = execution_evidence(
            "expected", endpoint_url=destination, is_ci=False, is_container=True
        )
        assert custom_headers == {}


def test_docker_launch_mounts_context_without_relying_on_ci_environment(tmp_path: Path) -> None:
    command = docker_command("runtime-image", tmp_path, ["opensre", "--record-install"])
    assert f"type=bind,source={tmp_path},target=/run/opensre,readonly" in command
    assert "OPENSRE_WIZARD_STORE_PATH=/opensre-home/opensre.json" in command
    assert "CI" not in command and "GITHUB_ACTIONS" not in command
    assert command[-3:] == ["runtime-image", "opensre", "--record-install"]


def test_native_launcher_overrides_inherited_profile(tmp_path: Path, monkeypatch) -> None:
    old_profile = tmp_path / "old"
    old_profile.mkdir()
    (old_profile / "anonymous_id").write_text("old-identity")
    output = tmp_path / "child.json"
    manifest = tmp_path / "manifest.json"
    monkeypatch.setenv("OPENSRE_WIZARD_STORE_PATH", str(old_profile / "opensre.json"))
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://fixture.invalid")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "mint-token")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setattr(runner_launcher, "fetch_github_token", lambda _identity: "fixture-token")
    command = (
        "import json,os,sys; from pathlib import Path; "
        "from config.constants.paths import get_store_path; "
        "profile=get_store_path().parent; "
        "Path(sys.argv[1]).write_text(json.dumps({'identity': (profile/'anonymous_id').read_text(), "
        "'mint_token': 'ACTIONS_ID_TOKEN_REQUEST_TOKEN' in os.environ})); "
        "(profile/'installed').touch()"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "launcher",
            "--manifest",
            str(manifest),
            "--require-delivery",
            "--",
            sys.executable,
            "-c",
            command,
            str(output),
        ],
    )
    assert runner_launcher.main() == 0
    result = json.loads(output.read_text())
    assert result["identity"] == json.loads(manifest.read_text())["analytics_id"]
    assert result["mint_token"] is False
    assert (old_profile / "anonymous_id").read_text() == "old-identity"
    assert not (old_profile / "installed").exists()
