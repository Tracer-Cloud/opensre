"""Install delivery across legacy markers, retries, and destination changes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest

from infrastructure.analytics import provider
from infrastructure.analytics.destination import AnalyticsDestination


@pytest.fixture
def deliveries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[list[dict[str, Any]]]:
    provider.shutdown_analytics(flush=True)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("OPENSRE_ANALYTICS_DISABLED", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
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


def test_legacy_marker_recovers_once_without_replacing_the_original_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deliveries: list[dict[str, Any]],
) -> None:
    identity = str(uuid.uuid4())
    (tmp_path / "anonymous_id").write_text(identity)
    (tmp_path / "installed").touch()

    assert provider.capture_install_detected_if_needed({"install_source": "posix_installer"})
    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed() is False

    assert len(deliveries) == 1
    event = deliveries[0]
    assert event["anonymous_id"] == identity
    assert event["event"] == "install_detected"
    assert event["event_id"] != f"install_detected:{identity}"
    assert event["properties"]["install_detection_reason"] == "unverified_marker"
    assert event["properties"]["install_source"] == "posix_installer"


@pytest.mark.parametrize("status", [HTTPStatus.SERVICE_UNAVAILABLE, HTTPStatus.OK])
def test_legacy_recovery_retries_until_explicit_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deliveries: list[dict[str, Any]],
    status: HTTPStatus,
) -> None:
    (tmp_path / "anonymous_id").write_text(str(uuid.uuid4()))
    (tmp_path / "installed").touch()
    accepted_post = httpx.Client.post

    def failed_post(_client, url, *, content, **_kwargs):
        deliveries.append(json.loads(content))
        return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.Client, "post", failed_post)
    assert provider.capture_install_detected_if_needed()
    restart(monkeypatch)
    monkeypatch.setattr(httpx.Client, "post", accepted_post)
    assert provider.capture_install_detected_if_needed()
    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed() is False
    assert len(deliveries) == 2
    assert deliveries[0]["event_id"] == deliveries[1]["event_id"]


def test_unwritable_receipt_is_logged_and_the_accepted_install_is_resent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deliveries: list[dict[str, Any]],
) -> None:
    # A file where the receipt directory belongs makes every receipt write fail.
    (tmp_path / "install-deliveries-v1").write_text("not a directory")

    assert provider.capture_install_detected_if_needed()
    provider.shutdown_analytics(flush=True, timeout=5)

    failures = (tmp_path / "analytics_errors.log").read_text()
    assert 'stage="install_receipt"' in failures
    assert "install-deliveries-v1" in failures
    # The legacy marker still lands, so the retry is a recovery, not a first install.
    assert (tmp_path / "installed").exists()

    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed()
    provider.shutdown_analytics(flush=True, timeout=5)
    assert [event["event"] for event in deliveries] == ["install_detected", "install_detected"]
    assert deliveries[1]["properties"]["install_detection_reason"] == "unverified_marker"


@pytest.mark.parametrize("change", ["identity", "destination"])
def test_delivery_receipt_is_scoped_to_identity_and_destination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deliveries: list[dict[str, Any]],
    change: str,
) -> None:
    assert provider.capture_install_detected_if_needed()
    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed() is False
    original_identity = (tmp_path / "anonymous_id").read_text()
    original_destination = provider.resolve_analytics_destination
    if change == "identity":
        (tmp_path / "anonymous_id").write_text(str(uuid.uuid4()))
    else:
        monkeypatch.setattr(
            provider,
            "resolve_analytics_destination",
            lambda: AnalyticsDestination("https://other.opensre.test/api/analytics/events"),
        )
    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed()
    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed() is False
    installs = [event for event in deliveries if event["event"] == "install_detected"]
    assert len(installs) == 2

    # Restoring an earlier scope must find its receipt, not redate its recovery.
    (tmp_path / "anonymous_id").write_text(original_identity)
    monkeypatch.setattr(provider, "resolve_analytics_destination", original_destination)
    restart(monkeypatch)
    assert provider.capture_install_detected_if_needed() is False
