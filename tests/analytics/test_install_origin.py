"""Original command origin remains immutable across delivery retries and reinstalls."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest

from infrastructure.analytics import event_properties, provider
from infrastructure.analytics.destination import AnalyticsDestination


@pytest.fixture
def deliveries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[list[dict[str, Any]]]:
    provider.shutdown_analytics(flush=True)
    for name in ("OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", tmp_path / "anonymous_id")
    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "_cached_anonymous_id", None)
    monkeypatch.setattr(provider, "_pending_user_id_load_failures", [])
    monkeypatch.setattr(provider, "_instance", None)
    monkeypatch.setattr(provider, "_install_capture_state", provider._InstallCaptureState())
    monkeypatch.setattr(provider.atexit, "register", lambda _callback: None)
    monkeypatch.setattr(
        provider,
        "resolve_analytics_destination",
        lambda: AnalyticsDestination("https://app.opensre.test/api/analytics/events"),
    )
    posted: list[dict[str, Any]] = []

    def post(_client, url, *, content, **_kwargs):
        posted.append(json.loads(content))
        return httpx.Response(HTTPStatus.ACCEPTED, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", post)
    try:
        yield posted
    finally:
        provider.shutdown_analytics(flush=True, timeout=5)


def restart(monkeypatch: pytest.MonkeyPatch) -> None:
    provider.shutdown_analytics(flush=True, timeout=5)
    monkeypatch.setattr(provider, "_instance", None)
    monkeypatch.setattr(provider, "_cached_anonymous_id", None)
    monkeypatch.setattr(provider, "_install_capture_state", provider._InstallCaptureState())


@pytest.mark.parametrize("original_origin", ["github", ""])
def test_retry_and_reinstall_keep_original_origin_including_unknown(
    monkeypatch: pytest.MonkeyPatch,
    deliveries: list[dict[str, Any]],
    original_origin: str,
) -> None:
    def unsupported_hard_links(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("Hard links are unsupported on this configuration volume")

    monkeypatch.setattr(os, "link", unsupported_hard_links)
    accepted_post = httpx.Client.post

    def failed_post(_client, url, *, content, **_kwargs):
        deliveries.append(json.loads(content))
        return httpx.Response(HTTPStatus.SERVICE_UNAVAILABLE, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", failed_post)
    monkeypatch.setenv("OPENSRE_INSTALL_ORIGIN", original_origin)
    monkeypatch.setenv("OPENSRE_INSTALL_SOURCE", "posix_installer")
    original = event_properties.build_install_detected_properties(entrypoint="opensre")
    assert provider.capture_install_detected_if_needed(original)
    restart(monkeypatch)

    monkeypatch.setattr(httpx.Client, "post", accepted_post)
    monkeypatch.setenv("OPENSRE_INSTALL_ORIGIN", "landing_page")
    monkeypatch.setenv("OPENSRE_INSTALL_SOURCE", "powershell_installer")
    monkeypatch.setitem(provider._BASE_PROPERTIES, "cli_version", "new-version")
    later = event_properties.build_install_detected_properties(entrypoint="opensre")
    assert provider.capture_install_detected_if_needed(later)
    restart(monkeypatch)

    assert len(deliveries) == 2
    assert deliveries[0] == deliveries[1]
    assert deliveries[1]["properties"].get("install_origin", "") == original_origin
    assert provider.capture_install_detected_if_needed(later) is False
    restart(monkeypatch)
    assert len(deliveries) == 2


def test_legacy_installation_cannot_be_attributed_by_a_tagged_reinstall(
    tmp_path: Path, deliveries: list[dict[str, Any]]
) -> None:
    (tmp_path / "installed").touch()
    assert provider.capture_install_detected_if_needed({"install_origin": "github"}) is False
    assert deliveries == []
    assert not (tmp_path / "install-events-v1").exists()


def test_concurrent_processes_publish_one_complete_observation(tmp_path: Path) -> None:
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    from infrastructure.analytics.install_delivery import persist_observation

    identity = str(uuid.uuid4())
    bodies = [
        json.dumps(
            {
                "anonymous_id": identity,
                "event": "install_detected",
                "event_id": f"install_detected:{identity}",
                "occurred_at": f"2026-09-21T12:00:0{index}Z",
                "properties": {"install_source": str(index)},
            }
        ).encode()
        for index in range(6)
    ]
    with ProcessPoolExecutor(max_workers=6, mp_context=get_context("spawn")) as pool:
        futures = [pool.submit(persist_observation, tmp_path, identity, body) for body in bodies]
        delivered = [future.result(timeout=30) for future in futures]
    assert len(set(delivered)) == 1
    assert delivered[0] in bodies
    assert not list((tmp_path / "install-events-v1").glob("*.tmp"))
