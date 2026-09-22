"""Connect the Python client to the real webapp route and isolated ClickHouse."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from infrastructure.analytics.destination import AnalyticsDestination

_REPO = Path(__file__).resolve().parents[2]
_TOKEN_A = "osre_pat_integrity_a_not_a_real_token"


def isolated_environment() -> dict[str, str]:
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
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


@pytest.fixture
def receiver(tmp_path: Path) -> Iterator[dict[str, Any]]:
    checkout = os.getenv("OPENSRE_WEBAPP_CHECKOUT")
    if not checkout:
        pytest.skip("Set OPENSRE_WEBAPP_CHECKOUT to run the real HTTP/ClickHouse connection tests.")
    assert checkout is not None
    server = Path(checkout) / "scripts/commit-integrity-server.mjs"
    assert server.is_file(), "The webapp checkout must include the commit integrity receiver."
    env = isolated_environment()
    env["INTEGRITY_CLICKHOUSE_URL"] = os.getenv(
        "INTEGRITY_CLICKHOUSE_URL", "http://127.0.0.1:18123"
    )
    with (tmp_path / "receiver.log").open("w+") as log:
        process = subprocess.Popen(
            ["node", str(server)],
            cwd=checkout,
            env=env,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        try:
            assert process.stdout is not None
            output: Queue[str] = Queue()
            Thread(target=lambda: output.put(process.stdout.readline()), daemon=True).start()
            line = output.get(timeout=30)
            assert line, "Integrity receiver failed to start; inspect receiver.log."
            info = json.loads(line)
            assert urlsplit(info["url"]).hostname == "127.0.0.1"
            yield info
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=20)


def emit(root: Path, receiver: dict[str, Any], scenario: str) -> subprocess.CompletedProcess[str]:
    env = isolated_environment()
    env.update(
        {
            "PYTHONPATH": str(_REPO),
            "OPENSRE_HOME": str(root),
            "OPENSRE_WIZARD_STORE_PATH": str(root / "opensre.json"),
            "OPENSRE_ACCOUNT_METADATA_PATH": str(root / "account.json"),
            "OPENSRE_APP_URL": receiver["url"],
            "OPENSRE_DISABLE_KEYRING": "0",
            "OPENSRE_SENTRY_DISABLED": "1",
            "OPENSRE_LANGFUSE_DISABLED": "1",
        }
    )
    if scenario == "silo":
        env.update(
            {
                "OPENSRE_WEBAPP_URL": receiver["url"],
                "AGENT_USAGE_SECRET": "integrity_fixture_silo_not_a_real_secret",
                "ORGANIZATION_ID": "org_integrity",
            }
        )
    result = subprocess.run(
        [sys.executable, str(_REPO / "tests/analytics/_commit_connection_process.py"), scenario],
        cwd=_REPO,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(result.stdout)["emitted"] >= 1
    return result


def evidence(receiver: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(f"{receiver['url']}/__test/evidence", timeout=10)
    response.raise_for_status()
    return response.json()


def replay(
    receiver: dict[str, Any],
    payload: dict[str, Any],
    *,
    token: str = _TOKEN_A,
    tamper: bool = False,
) -> httpx.Response:
    body = json.dumps(payload, separators=(",", ":")).encode()
    destination = AnalyticsDestination(f"{receiver['url']}/api/analytics/events", token)
    headers = destination.headers(body)
    if tamper:
        body = body.replace(b'"repair"', b'"tampered"')
    return httpx.post(destination.endpoint_url, content=body, headers=headers, timeout=10)


def commits(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in result["rows"] if row["event_type"] == "opensre_commit_created"]


def test_client_events_persist_for_two_users_and_replay_counts_once(
    tmp_path: Path, receiver: dict[str, Any]
) -> None:
    root = tmp_path / "client"
    emit(root, receiver, "a")
    emit(root, receiver, "saved")
    emit(root, receiver, "b")
    result = evidence(receiver)
    requests = [r for r in result["requests"] if r["payload"]["event"] == "opensre_commit_created"]
    assert len(requests) == 3
    assert {r["status"] for r in requests} == {HTTPStatus.ACCEPTED}
    rows = commits(result)
    assert Counter(row["user_id"] for row in rows) == {"user_integrity_a": 2, "user_integrity_b": 1}
    assert len({row["analytics_id"] for row in rows}) == 1
    sent = {r["payload"]["event_id"]: r["payload"] for r in requests}
    for row in rows:
        payload = sent[row["event_id"]]
        assert row["schema_version"] == payload["schema_version"] == 1
        assert row["source"] == payload["source"] == "opensre_runtime"
        assert row["analytics_id"] == payload["anonymous_id"]
        actual = datetime.fromisoformat(row["occurred_at"]).replace(tzinfo=UTC)
        expected = datetime.fromisoformat(payload["occurred_at"])
        assert abs((actual - expected).total_seconds()) < 0.001
        assert json.loads(row["properties_json"]) == payload["properties"]
        assert payload["properties"]["workflow"] == "github_ci_fix"
        assert payload["properties"]["commit_kind"] == "repair"
        assert int(payload["properties"]["changed_file_count"]) == 2
        assert row["authenticated"] == row["signature_verified"] == 1
        assert row["auth_kind"] == "personal"
    assert replay(receiver, requests[0]["payload"]).status_code == HTTPStatus.ACCEPTED
    assert Counter(row["user_id"] for row in commits(evidence(receiver))) == {
        "user_integrity_a": 2,
        "user_integrity_b": 1,
    }


def test_account_switch_and_silo_never_impersonate_a_person(
    tmp_path: Path, receiver: dict[str, Any]
) -> None:
    emit(tmp_path / "personal", receiver, "switch")
    emit(tmp_path / "silo", receiver, "silo")
    rows = commits(evidence(receiver))
    assert Counter(row["user_id"] for row in rows) == {
        "user_integrity_a": 1,
        "user_integrity_b": 1,
        "": 1,
    }
    silo = next(row for row in rows if row["auth_kind"] == "silo")
    assert silo["organization_id"] == "org_integrity"
    assert silo["authenticated"] == silo["signature_verified"] == 1


def test_tagged_completion_is_stored_but_excluded_from_graph(
    tmp_path: Path, receiver: dict[str, Any]
) -> None:
    dashboard = os.getenv("OPENSRE_ANALYTICS_CHECKOUT")
    if not dashboard:
        pytest.skip("Set OPENSRE_ANALYTICS_CHECKOUT to verify the production graph SQL.")
    root = tmp_path / "client"
    for scenario in ["a", "saved", "b", "tagged"]:
        emit(root, receiver, scenario)
    result = evidence(receiver)
    assert len(commits(result)) == 4
    tagged = [row for row in commits(result) if "e2e_run_id" in json.loads(row["properties_json"])]
    assert len(tagged) == 1
    first = next(
        request["payload"]
        for request in result["requests"]
        if request["payload"]["event"] == "opensre_commit_created"
    )
    assert replay(receiver, first).status_code == HTTPStatus.ACCEPTED
    env = isolated_environment()
    env.update(
        {
            "CLICKHOUSE_URL": os.getenv("INTEGRITY_CLICKHOUSE_URL", "http://127.0.0.1:18123"),
            "CLICKHOUSE_USERNAME": "test",
            "CLICKHOUSE_PASSWORD": "test-password",
            "CLICKHOUSE_DATABASE": receiver["database"],
        }
    )
    graph = subprocess.run(
        ["node", "--experimental-strip-types", "tests/helpers/commit-connection-query.mjs"],
        cwd=Path(dashboard) / "src",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert graph.returncode == 0, graph.stderr
    assert json.loads(graph.stdout) == {"total": 3, "a": 2, "b": 1}


def test_rejected_requests_do_not_reach_storage(tmp_path: Path, receiver: dict[str, Any]) -> None:
    emit(tmp_path / "client", receiver, "a")
    payload = next(
        request["payload"]
        for request in evidence(receiver)["requests"]
        if request["payload"]["event"] == "opensre_commit_created"
    )
    assert (
        replay(receiver, payload, token="invalid_fixture_token").status_code
        == HTTPStatus.UNAUTHORIZED
    )
    assert replay(receiver, payload, tamper=True).status_code == HTTPStatus.UNAUTHORIZED
    malformed = {**payload, "properties": {**payload["properties"], "changed_file_count": -1}}
    assert replay(receiver, malformed).status_code == HTTPStatus.BAD_REQUEST
    assert len(commits(evidence(receiver))) == 1


@pytest.mark.parametrize(
    "mode,signal",
    [("storage-failed", str(HTTPStatus.SERVICE_UNAVAILABLE.value)), ("timeout", "ReadTimeout")],
)
def test_delivery_failure_is_not_an_acknowledgement(
    tmp_path: Path, receiver: dict[str, Any], mode: str, signal: str
) -> None:
    httpx.post(f"{receiver['url']}/__test/mode", json={"mode": mode}).raise_for_status()
    emit(tmp_path / "client", receiver, "a")
    errors = (tmp_path / "client" / "analytics_errors.log").read_text()
    assert "analytics_send" in errors
    assert signal in errors
    httpx.post(f"{receiver['url']}/__test/mode", json={"mode": "normal"}).raise_for_status()
    assert commits(evidence(receiver)) == []
