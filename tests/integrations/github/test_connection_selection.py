"""GitHub grant selection for a hosted gateway that does not share the laptop catalog."""

from __future__ import annotations

from typing import Any

import pytest

from config.constants import GH_TOKEN_ENV
from core.tool.execution import availability_view
from integrations.github.connections import classify_github_connections, select_github_connection
from integrations.github.tools.github_cli.tool import github_cli

_LAPTOP = "local-9f3a"
_ORG = "org-acme"
_OTHER = "org-other"


def _grant(
    connection_id: str,
    *,
    available: bool,
    is_default: bool,
    token: str,
) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "integration_id": connection_id,
        "is_default": is_default,
        "config": {"auth_token": token} if token else {},
        "available": available,
    }


def _catalog(*grants: dict[str, Any], managed: bool = True) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "_all_github_instances": list(grants),
        "github": {"auth_token": "classified-before-selection"},
    }
    if managed:
        resolved["_github_managed_connections"] = True
    return resolved


def _cli_available(selected: dict[str, Any]) -> bool:
    tool = github_cli.__opensre_registered_tool__
    return bool(tool.is_available(availability_view(selected)))


def test_an_unknown_laptop_connection_keeps_the_single_org_grant() -> None:
    """The hosted probe forwards this machine's id; the gateway only has the org grant."""
    selected = select_github_connection(
        _catalog(_grant(_ORG, available=True, is_default=True, token="gho_acme")),
        _LAPTOP,
    )

    github = selected["github"]
    assert "connection_selection_error" not in github
    assert github["connection_id"] == _ORG
    assert github["auth_token"] == "gho_acme"
    assert _cli_available(selected) is True


def test_an_unknown_id_does_not_pick_among_several_grants() -> None:
    """A choice this process does not have must not fall through to another account."""
    selected = select_github_connection(
        _catalog(
            _grant(_ORG, available=True, is_default=False, token="gho_acme"),
            _grant(_OTHER, available=True, is_default=False, token="gho_other"),
            managed=False,
        ),
        _LAPTOP,
    )

    github = selected["github"]
    assert github["connection_selection_error"] == "github_connection_required"
    assert github["connection_id"] == _LAPTOP
    assert github.get("auth_token") != "gho_acme"
    assert _cli_available(selected) is False


def test_one_available_grant_is_used_when_it_is_not_the_default() -> None:
    """A shell with no connection id still reaches the org's only live grant."""
    selected = select_github_connection(
        _catalog(_grant(_ORG, available=True, is_default=False, token="gho_acme")),
        None,
    )

    github = selected["github"]
    assert "connection_selection_error" not in github
    assert github["connection_id"] == _ORG
    assert github["auth_token"] == "gho_acme"


def test_a_named_dead_grant_stays_unavailable_even_with_an_env_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection this process has, but cannot use, does not fall back to another account."""
    monkeypatch.setenv(GH_TOKEN_ENV, "gho_env")
    selected = select_github_connection(
        _catalog(
            _grant(_ORG, available=True, is_default=True, token="gho_acme"),
            _grant(_OTHER, available=False, is_default=False, token=""),
        ),
        _OTHER,
    )

    github = selected["github"]
    assert github["connection_selection_error"] == "github_connection_unavailable"
    assert github["connection_id"] == _OTHER
    assert github.get("auth_token") != "gho_acme"
    assert _cli_available(selected) is False


def test_two_live_grants_without_a_default_are_not_guessed() -> None:
    selected = select_github_connection(
        _catalog(
            _grant(_ORG, available=True, is_default=False, token="gho_acme"),
            _grant(_OTHER, available=True, is_default=False, token="gho_other"),
        ),
        None,
    )

    assert selected["github"]["connection_selection_error"] == "github_connection_required"
    assert selected["github"]["connection_id"] == ""
    assert "auth_token" not in selected["github"]


def test_a_dead_default_does_not_fall_through_to_another_live_grant() -> None:
    selected = select_github_connection(
        _catalog(
            _grant(_ORG, available=False, is_default=True, token=""),
            _grant(_OTHER, available=True, is_default=False, token="gho_other"),
        ),
        None,
    )

    github = selected["github"]
    assert github["connection_selection_error"] == "github_connection_unavailable"
    assert github["connection_id"] == ""
    assert github.get("auth_token") != "gho_other"


def test_an_unset_default_marker_does_not_turn_local_env_into_managed_connections() -> None:
    resolved = {"github": {"connection_verified": True}}
    classify_github_connections(
        [
            {
                "id": "env-github",
                "service": "github",
                "status": "active",
                "credentials": {
                    "url": "https://api.githubcopilot.com/mcp/",
                    "auth_token": "",
                    "is_default": None,
                },
            }
        ],
        resolved,
    )

    assert "_github_managed_connections" not in resolved
    assert resolved["github"] == {"connection_verified": True}
