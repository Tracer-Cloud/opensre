"""Tests for Azure OpenAI wizard onboarding helpers."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from surfaces.cli.wizard import azure_openai, components


def test_choose_azure_deployment_lists_resource_deployments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        azure_openai,
        "discover_azure_openai_deployments_from_env",
        lambda: ["gpt-4.1", "my-custom-deployment"],
    )

    captured: dict[str, list[str]] = {}

    def _mock_select(_prompt: str, choices: list[Any], **_kwargs: Any) -> Any:
        captured["values"] = [choice.value for choice in choices]
        m = MagicMock()
        m.ask.return_value = "gpt-4.1"
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)

    deployment = azure_openai.choose_azure_deployment(default="")

    assert deployment == "gpt-4.1"
    assert captured["values"][:2] == ["gpt-4.1", "my-custom-deployment"]
    assert captured["values"][-1] == components.CUSTOM_MODEL_SENTINEL


def test_choose_azure_deployment_prompts_manual_entry_when_discovery_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(azure_openai, "discover_azure_openai_deployments_from_env", lambda: [])
    monkeypatch.setattr(
        azure_openai,
        "prompt_value",
        lambda *_args, **_kwargs: "manual-deployment",
    )

    deployment = azure_openai.choose_azure_deployment(default="")

    assert deployment == "manual-deployment"
