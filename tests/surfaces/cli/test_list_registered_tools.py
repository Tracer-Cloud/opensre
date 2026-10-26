"""Tests for listing registered tools by integration."""

from __future__ import annotations

from types import SimpleNamespace

from integrations.registry import INTEGRATION_SPECS
from surfaces.cli import list_registered_tools
from surfaces.cli.list_registered_tools import (
    get_integration_from_module,
    get_integration_names,
    get_registered_tools_by_integration,
)
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES


def _expected_integration_names() -> set[str]:
    return {
        *{spec.service for spec in INTEGRATION_SPECS},
        *{package.split(".")[1] for package in INTEGRATION_TOOL_PACKAGES},
    }


def test_get_integration_names_includes_specs_and_tool_packages() -> None:
    assert get_integration_names() == _expected_integration_names()


def test_get_integration_from_module_only_matches_integration_tools() -> None:
    assert get_integration_from_module("integrations.aws.tools.foo") == "aws"
    assert get_integration_from_module("integrations.aws.verifier") is None


def test_registered_tools_are_sorted_and_grouped() -> None:
    result = get_registered_tools_by_integration()

    assert list(result) == sorted(result)
    assert _expected_integration_names() <= set(result)

    for tools in result.values():
        assert tools == sorted(tools)


def test_registered_tools_are_grouped_by_integration(monkeypatch) -> None:
    monkeypatch.setattr(
        list_registered_tools,
        "get_integration_names",
        lambda: {"github", "redis", "trello"},
    )
    monkeypatch.setattr(
        list_registered_tools,
        "get_registered_tools",
        lambda: [
            SimpleNamespace(
                name="redis_b",
                origin_module="integrations.redis.tools.foo",
            ),
            SimpleNamespace(
                name="github_tool",
                origin_module="integrations.github.tools.bar",
            ),
            SimpleNamespace(
                name="redis_a",
                origin_module="integrations.redis.tools.baz",
            ),
            SimpleNamespace(
                name="core_tool",
                origin_module="tools.system.foo",
            ),
        ],
    )
    assert list_registered_tools.get_registered_tools_by_integration() == {
        "github": ["github_tool"],
        "redis": ["redis_a", "redis_b"],
        "trello": [],
    }


def test_main_shows_integrations_with_no_tools(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        list_registered_tools,
        "get_registered_tools_by_integration",
        lambda: {
            "empty": [],
            "github": ["search_github_issues"],
        },
    )

    list_registered_tools.main()

    assert capsys.readouterr().out == (
        "empty:\n  (no registered tools)\ngithub:\n  - search_github_issues\n"
    )
