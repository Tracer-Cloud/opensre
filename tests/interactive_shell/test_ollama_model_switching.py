"""Ollama discovery and cancellation use the shared selection boundary."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from surfaces.shared.llm_setup import model_discovery, ollama
from surfaces.shared.llm_setup.auth_results import SelectionError
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


def test_model_menu_choices_come_from_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama, "list_available_models", lambda _host: ("llama3.2:latest", "qwen2.5:7b")
    )
    assert model_discovery.discover_models(PROVIDER_BY_VALUE["ollama"]) == [
        ("llama3.2:latest", "llama3.2:latest"),
        ("qwen2.5:7b", "qwen2.5:7b"),
    ]


def test_unavailable_model_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(ollama, "list_available_models", lambda _host: ("llama3.2:latest",))
    with pytest.raises(SelectionError, match="Ollama model is not available: ghost-model"):
        model_discovery.validate_available_model(PROVIDER_BY_VALUE["ollama"], "ghost-model")


def test_model_detection_can_be_cancelled(monkeypatch) -> None:
    from surfaces.interactive_shell.command_registry.model.switching import switch_llm_provider

    def cancel(_host: str) -> tuple[str, ...]:
        raise KeyboardInterrupt

    monkeypatch.setattr(ollama, "list_available_models", cancel)
    output = io.StringIO()
    assert switch_llm_provider("ollama", Console(file=output)) is False
    assert "cancelled" in output.getvalue()
