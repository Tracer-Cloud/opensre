"""Hosted LLM requests name the member they are spent for, per request.

Settings → Usage charts credits per member from the ``X-OpenSRE-Actor`` header.
A hosted gateway caches one client per role and runs turns in parallel, so the
header must come from the turn making each request, never from the client.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

import pytest

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.llm.client_builders import build_agent_client, build_reasoning_client
from core.llm.types import LLMRoute

_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-account",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
}


class _HostedRoute:
    """A stand-in webapp LLM route that records the actor header of each request."""

    def __init__(self) -> None:
        self.actors: list[str | None] = []
        route = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
                return None

            def do_POST(self) -> None:  # noqa: N802
                self.rfile.read(int(self.headers.get("content-length") or 0))
                route.actors.append(self.headers.get("x-opensre-actor"))
                body = json.dumps(_COMPLETION).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/api/llm/v1"


@pytest.fixture
def hosted_route(monkeypatch: pytest.MonkeyPatch) -> Iterator[_HostedRoute]:
    route = _HostedRoute()
    route.thread.start()

    def account_route() -> SimpleNamespace:
        return SimpleNamespace(base_url=route.base_url, model="gpt-account")

    def account_token() -> str:
        return "osre_gateway_token"

    monkeypatch.setattr("config.account.account_llm_route", account_route)
    monkeypatch.setattr("config.account.resolve_account_token", account_token)
    try:
        yield route
    finally:
        route.server.shutdown()
        route.server.server_close()


def _route() -> LLMRoute:
    return LLMRoute(
        settings=SimpleNamespace(provider="openai", openai_reasoning_model="gpt-r"),
        provider="openai",
        cli_provider_registration=None,
        use_litellm=False,
    )


def _agent_sdk() -> Any:
    return build_agent_client(_route())._client


def _reasoning_sdk() -> Any:
    return build_reasoning_client(_route(), "reasoning")._ensure_client()


def _turn(member: str) -> StorageScope:
    return StorageScope(principal=Principal.org("org_test"), actor=Actor(id=member))


@pytest.mark.parametrize("sdk_client", [_agent_sdk, _reasoning_sdk])
def test_one_shared_hosted_client_names_each_turns_own_member(
    hosted_route: _HostedRoute, sdk_client: Callable[[], Any]
) -> None:
    # Arrange: one client, as the per-role cache hands it to every turn
    sdk = sdk_client()

    def ask() -> None:
        sdk.chat.completions.create(
            model="gpt-account", messages=[{"role": "user", "content": "hi"}]
        )

    # Act
    with bound_storage_scope(_turn("U0ALICE")):
        ask()
    with bound_storage_scope(_turn("user_2bobBOBbobBOB")):
        ask()
    ask()

    # Assert: each request carries its own turn's member; outside a turn, none
    assert hosted_route.actors == ["U0ALICE", "user_2bobBOBbobBOB", None]
