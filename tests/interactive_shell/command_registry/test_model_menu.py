"""Nested model-menu cancellation preserves focus without changing configuration."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry.model import command


@pytest.fixture
def menu(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(command, "current_model_selection", lambda: ("openai", "active", "tools"))

    def unexpected_mutation(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("Backing out must not change model configuration")

    monkeypatch.setattr(command, "switch_llm_provider", unexpected_mutation)
    monkeypatch.setattr(command, "switch_toolcall_model", unexpected_mutation)
    return calls


def test_toolcall_escape_returns_to_reasoning_choice_then_provider(
    monkeypatch: pytest.MonkeyPatch, menu: list[dict[str, Any]]
) -> None:
    choices = iter(["openai", "selected-model", None, None, None])

    def choose(**kwargs: Any) -> str | None:
        menu.append(kwargs)
        return next(choices)

    monkeypatch.setattr(command, "repl_choose_one", choose)
    assert command._interactive_set_provider(Console(file=StringIO())) is None
    assert [call["title"] for call in menu] == [
        "LLM provider",
        "Reasoning model",
        "Tool-call model",
        "Reasoning model",
        "LLM provider",
    ]
    assert menu[3]["initial_value"] == "selected-model"
    assert menu[3]["current_value"] == "active"
    assert ("active", "active") in menu[3]["choices"]
    assert ("tools", "tools") in menu[2]["choices"]
    assert menu[4]["initial_value"] == "openai"
    assert all(call["panel"] for call in menu)


def test_other_provider_escape_returns_through_each_level(
    monkeypatch: pytest.MonkeyPatch, menu: list[dict[str, Any]]
) -> None:
    choices = iter([command.OTHER_PROVIDER_SELECTION, "anthropic", None, None, None])

    def choose(**kwargs: Any) -> str | None:
        menu.append(kwargs)
        return next(choices)

    monkeypatch.setattr(command, "repl_choose_one", choose)
    assert command._interactive_set_provider(Console(file=StringIO())) is None
    assert [call["title"] for call in menu] == [
        "LLM provider",
        "Other providers",
        "Reasoning model",
        "Other providers",
        "LLM provider",
    ]
    assert menu[3]["initial_value"] == "anthropic"
    assert menu[4]["initial_value"] == command.OTHER_PROVIDER_SELECTION


def test_cancel_custom_toolcall_returns_to_picker_without_mutation(
    monkeypatch: pytest.MonkeyPatch, menu: list[dict[str, Any]]
) -> None:
    choices = iter(["openai", "__custom__", None, None])

    def choose(**kwargs: Any) -> str | None:
        menu.append(kwargs)
        return next(choices)

    monkeypatch.setattr(command, "repl_choose_one", choose)
    monkeypatch.setattr(command, "_prompt_custom_model_id", lambda *_: None)
    assert command._interactive_set_toolcall(Console(file=StringIO())) is None
    assert [call["title"] for call in menu] == [
        "LLM provider",
        "Tool-call model",
        "Tool-call model",
        "LLM provider",
    ]
    assert menu[2]["initial_value"] == "__custom__"


def test_nonfeatured_current_provider_marks_both_bucket_and_provider(
    monkeypatch: pytest.MonkeyPatch, menu: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(
        command, "current_model_selection", lambda: ("anthropic", "active", "tools")
    )
    choices = iter([command.OTHER_PROVIDER_SELECTION, None, None])

    def choose(**kwargs: Any) -> str | None:
        menu.append(kwargs)
        return next(choices)

    monkeypatch.setattr(command, "repl_choose_one", choose)
    assert (
        command._choose_provider_value(title="Provider", breadcrumb="/model › Change model") is None
    )
    assert menu[0]["current_value"] == command.OTHER_PROVIDER_SELECTION
    assert menu[1]["current_value"] == "anthropic"
    assert menu[2]["current_value"] == command.OTHER_PROVIDER_SELECTION
