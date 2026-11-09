"""One person's Slack and web app ids resolve to one member, and an outage never invents one.

The answer decides who may see and run a private loop, which acts with its
owner's own accounts, so a failure may only hide a loop, never show it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import httpx
import pytest

import integrations.webapp_requests.members as member_identity
from integrations.webapp_requests import WebappAgentTarget


@dataclass
class _Response:
    status_code: int
    body: dict[str, Any] = field(default_factory=dict)

    def json(self) -> dict[str, Any]:
        return self.body


@dataclass
class _Webapp:
    """``GET /api/agent/members``: answers from ``members`` until ``down``."""

    members: dict[str, str]
    down: bool = False
    calls: list[str] = field(default_factory=list)

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        assert (method, url) == ("GET", "https://app.example/api/agent/members")
        actor = kwargs["params"]["actor"]
        self.calls.append(actor)
        if self.down:
            raise httpx.ConnectError("down")
        return _Response(
            HTTPStatus.OK, {"success": True, "data": {"userId": self.members.get(actor)}}
        )


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    member_identity.reset_member_identity_cache()
    yield
    member_identity.reset_member_identity_cache()


def test_an_outage_keeps_known_members_and_resolves_nobody_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    webapp = _Webapp(members={"U0OWNER": "user_owner"})
    target = WebappAgentTarget(base_url="https://app.example", organization="org_A", token="t")
    monkeypatch.setattr(member_identity, "webapp_agent_target", lambda: target)
    monkeypatch.setattr(httpx, "request", webapp.request)
    now = [0.0]
    monkeypatch.setattr(member_identity.time, "monotonic", lambda: now[0])

    # Act: resolve while up, then again past the cache window while down.
    known = member_identity.member_user_id("U0OWNER")
    webapp.down = True
    now[0] = 3_600.0
    kept = member_identity.member_user_id("U0OWNER")
    unknown = member_identity.member_user_id("U0STRANGER")

    # Assert
    assert (known, kept, unknown) == ("user_owner", "user_owner", None)
    assert webapp.calls == ["U0OWNER", "U0OWNER", "U0STRANGER"]
