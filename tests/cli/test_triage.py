"""Demo ingress uses the live gateway address rather than a later CLI environment."""

from __future__ import annotations

import httpx
import pytest

from surfaces.cli.commands import gateway


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
        return httpx.Response(200, json={"status": "ready"}, request=httpx.Request("GET", url))

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
