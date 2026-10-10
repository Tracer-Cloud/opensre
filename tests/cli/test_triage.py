"""Demo ingress uses the live gateway address rather than a later CLI environment."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from surfaces.cli.commands import gateway


def test_source_removal_retries_credential_cleanup_after_revocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.domain.alerts.triage.storage import TriageStore
    from surfaces.cli.commands import triage
    from tests.core.domain.alerts.triage.test_store import source

    store = TriageStore(tmp_path / "triage.sqlite3")
    store.add_source(source())
    monkeypatch.setattr(triage, "TriageStore", lambda: store)
    attempts = []

    def remove(ref: str) -> None:
        attempts.append(ref)
        if len(attempts) == 1:
            raise RuntimeError("Credential file locked; retry removal")

    monkeypatch.setattr(triage, "delete_credential", remove)
    runner = CliRunner()
    first = runner.invoke(triage.triage_command, ["remove", "test"])
    assert first.exit_code == 1
    assert store.source("test").removed
    second = runner.invoke(triage.triage_command, ["remove", "test"])
    assert second.exit_code == 0
    assert attempts == ["TEST_KEY", "TEST_KEY"]


def test_source_removal_retains_a_shared_managed_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.domain.alerts.triage.storage import TriageStore
    from surfaces.cli.commands import triage
    from tests.core.domain.alerts.triage.test_store import source

    store = TriageStore(tmp_path / "triage.sqlite3")
    store.add_source(source().model_copy(update={"credential_kind": "environment"}))
    monkeypatch.setattr(triage, "TriageStore", lambda: store)
    monkeypatch.setattr(
        triage, "delete_credential", lambda _ref: pytest.fail("Removal deleted shared credentials")
    )
    result = CliRunner().invoke(triage.triage_command, ["remove", "test"])
    assert result.exit_code == 0
    assert store.source("test").removed


def test_demo_gateway_port_comes_from_verified_running_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setattr(
        gateway,
        "read_component_status",
        lambda: {"web": "serving http://0.0.0.0:9000 (health, alerts)"},
    )
    requested = []

    def response(url: str, **_kwargs: object) -> httpx.Response:
        requested.append(url)
        return httpx.Response(
            HTTPStatus.OK, json={"status": "ready"}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(gateway.httpx, "get", response)
    assert gateway.verified_gateway_web_port() == 9000
    assert requested == ["http://127.0.0.1:9000/readyz"]


@pytest.mark.parametrize("status", ["", "failed (address in use)", "serving"])
def test_demo_rejects_a_gateway_without_a_published_web_address(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setattr(gateway, "read_component_status", lambda: {"web": status})
    with pytest.raises(RuntimeError, match="Gateway web address is unavailable"):
        gateway.verified_gateway_web_port()
