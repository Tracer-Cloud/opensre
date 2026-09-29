"""Installation grain and account linkage probes against fresh runtime processes."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]


def run_process(
    root: Path,
    scenario: str = "start",
    *,
    ci: bool = False,
    silo: bool = False,
    cicd: bool = False,
    execution_context: Path | None = None,
    app_url: str = "https://integrity.invalid",
    gateway_token: bool = False,
) -> dict[str, Any]:
    # Keep OS process-launch variables, but never inherit developer credentials,
    # analytics destinations, or home-store overrides into this subprocess.
    allowed = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update(
        {
            "PYTHONPATH": str(_REPO),
            "OPENSRE_HOME": str(root),
            "OPENSRE_WIZARD_STORE_PATH": str(root / "opensre.json"),
            "OPENSRE_ACCOUNT_METADATA_PATH": str(root / "account.json"),
            "OPENSRE_APP_URL": app_url,
            "OPENSRE_DISABLE_KEYRING": "0",
            "OPENSRE_SENTRY_DISABLED": "1",
        }
    )
    if ci:
        env["GITHUB_ACTIONS"] = "true"
    if execution_context is not None:
        env["OPENSRE_EXECUTION_CONTEXT_PATH"] = str(execution_context)
    if cicd:
        env["OPENSRE_CICD"] = "1"
    if silo:
        env.update(
            {
                "OPENSRE_WEBAPP_URL": "https://integrity.invalid",
                "AGENT_USAGE_SECRET": "integrity_fixture_silo_not_a_real_secret",
                "ORGANIZATION_ID": "org_integrity_fixture",
            }
        )
    if gateway_token:
        # The webapp-minted LLM credential a Fargate task also carries. It must
        # never displace the silo analytics destination or sign analytics events.
        env["OPENSRE_ACCOUNT_TOKEN"] = "osre_gw_org_integrity_fixture." + "a" * 43
    completed = subprocess.run(
        [sys.executable, str(_REPO / "tests/analytics/_integrity_process.py"), scenario],
        cwd=_REPO,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    result: dict[str, Any] = json.loads(completed.stdout)
    assert result["requests"], completed.stderr
    return result


def test_runner_context_survives_stripped_ci_environment(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    root.mkdir()
    identity = "84f31ef1-8334-4a04-993b-6bc090635fcf"
    (root / "anonymous_id").write_text(identity)
    context = tmp_path / "execution-context.json"
    context.write_text(
        json.dumps(
            {
                "version": 1,
                "analytics_id": identity,
                "execution_origin": "github_actions",
                "token": "fixture_token",
            }
        )
    )
    result = run_process(root, execution_context=context, app_url="https://app.opensre.com")
    for request in result["requests"]:
        properties = request["payload"]["properties"]
        assert properties["is_ci"] is True
        assert properties["automation_status"] == "reported"
        assert properties["execution_origin"] == "github_actions"
        assert request["runner_token_present"] is True
        assert "fixture_token" not in json.dumps(request["payload"])
    custom = run_process(root, execution_context=context)
    assert not any(r["runner_token_present"] for r in custom["requests"])
    context.unlink()
    restarted = run_process(root, execution_context=context)
    assert all(
        r["payload"]["properties"]["automation_status"] == "unknown" for r in restarted["requests"]
    )
    assert not any(r["runner_token_present"] for r in restarted["requests"])


def installs(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [r["payload"] for r in result["requests"] if r["payload"]["event"] == "install_detected"]


@pytest.mark.parametrize("scenario", ["start", "late_cicd_marker"])
def test_explicit_cicd_marker_classifies_a_fresh_process_without_vendor_signals(
    tmp_path: Path,
    scenario: str,
) -> None:
    result = run_process(tmp_path / "cicd", scenario, cicd=scenario == "start")
    properties = installs(result)[0]["properties"]
    assert properties["install_origin"] == "cicd"
    for request in result["requests"]:
        runtime = request["payload"]["properties"]
        assert runtime["is_ci"] is True
        assert runtime["cicd_marker"] is True
        assert runtime["execution_environment"] in {"ci", "ci_container"}
    record_evidence(f"explicit-cicd-marker-{scenario}", result)


def record_evidence(name: str, evidence: object) -> None:
    destination = os.getenv("INTEGRITY_EVIDENCE_DIR")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def test_persistent_storage_restarts_and_reinstalls_keep_one_install(tmp_path: Path) -> None:
    runs = [run_process(tmp_path / "persistent") for _ in range(3)]
    events = [event for run in runs for event in installs(run)]
    assert len(events) == 1
    identities = {r["payload"]["anonymous_id"] for run in runs for r in run["requests"]}
    assert len(identities) == 1
    assert events[0]["event_id"] == f"install_detected:{events[0]['anonymous_id']}"
    assert all(run["installed_marker"] for run in runs)
    record_evidence("persistent-restarts", runs)


def test_fresh_storage_counts_runtime_instances_even_when_disk_persistence_is_reported(
    tmp_path: Path,
) -> None:
    runs = [run_process(tmp_path / str(index), silo=True) for index in range(3)]
    events = [event for run in runs for event in installs(run)]
    assert len(events) == 3
    assert len({event["anonymous_id"] for event in events}) == 3
    assert {event["properties"]["identity_persistence"] for event in events} == {"disk"}
    assert {event["properties"]["install_source"] for event in events} == {"first_cli_invocation"}
    assert {event["properties"]["distribution"] for event in events} <= {
        "source_checkout",
        "editable_package",
        "installed_package",
    }
    assert len({event["properties"]["composite_fingerprint"] for event in events}) == 1
    assert all(request["auth_kind"] == "silo" for run in runs for request in run["requests"])
    assert not any(
        request["payload"]["event"] == "account_authenticated"
        for run in runs
        for request in run["requests"]
    )
    record_evidence("fresh-storage-is-runtime-instances-not-people", runs)


def test_fargate_silo_delivers_remote_prompt_turns_with_silo_authority(tmp_path: Path) -> None:
    """A Fargate-like environment (webapp URL + usage secret + gateway account
    token) chooses the silo analytics destination, and one remote prompt turn's
    events carry the prompt surface, its session, and the organization."""
    result = run_process(tmp_path, "remote_prompt", silo=True, gateway_token=True)
    assert all(r["auth_kind"] == "silo" for r in result["requests"])
    assert all(r["signature_valid"] for r in result["requests"])
    events = {r["payload"]["event"]: r["payload"]["properties"] for r in result["requests"]}
    for event_name in ("react_turn_completed", "gateway_turn_completed"):
        properties = events[event_name]
        assert properties["surface"] == "prompt"
        assert properties["session_id"] == "session_integrity_remote"
        assert properties["organization_id"] == "org_integrity_fixture"
    record_evidence("fargate-silo-remote-prompt", result)


def test_failed_delivery_retries_the_same_install_event_id(tmp_path: Path) -> None:
    rejected = run_process(tmp_path, "reject")
    accepted = run_process(tmp_path)
    restarted = run_process(tmp_path)
    assert not rejected["installed_marker"]
    assert accepted["installed_marker"]
    assert installs(rejected)[0]["event_id"] == installs(accepted)[0]["event_id"]
    assert installs(restarted) == []
    record_evidence("delivery-retry", [rejected, accepted, restarted])


def test_saved_personal_credentials_link_anonymous_id_and_sign_subsequent_processes(
    tmp_path: Path,
) -> None:
    login = run_process(tmp_path, "saved_credentials")
    restarted = run_process(tmp_path)
    requests = login["requests"]
    assert requests[0]["payload"]["event"] == "install_detected"
    assert requests[0]["auth_kind"] == "anonymous"
    authenticated = [r for r in requests if r["payload"]["event"] == "account_authenticated"]
    assert len(authenticated) == 1
    assert authenticated[0]["auth_kind"] == "personal"
    assert authenticated[0]["signature_valid"]
    assert len({r["payload"]["anonymous_id"] for r in requests + restarted["requests"]}) == 1
    assert all(r["auth_kind"] == "personal" for r in restarted["requests"])
    assert installs(restarted) == []
    # Server-side Clerk resolution remains outside this transport test's claim.
    assert all("user_id" not in r["payload"] for r in requests)
    record_evidence("anonymous-to-personal", [login, restarted])


@pytest.mark.parametrize("ci", [False, True], ids=["non-ci", "ci"])
def test_real_host_runtime_classification(tmp_path: Path, ci: bool) -> None:
    result = run_process(tmp_path, ci=ci)
    properties = installs(result)[0]["properties"]
    assert properties["is_ci"] is ci
    assert properties["os_family"].lower() == platform.system().lower()
    expected_container = os.getenv("INTEGRITY_EXPECT_CONTAINER")
    if expected_container is not None:
        assert properties["is_container"] is (expected_container == "1")
    expected = (
        "ci_container"
        if ci and properties["is_container"]
        else "ci"
        if ci
        else "container"
        if properties["is_container"]
        else "local"
    )
    assert properties["execution_environment"] == expected
    record_evidence(f"runtime-{expected}", result)
