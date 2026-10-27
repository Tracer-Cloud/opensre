"""Tests for Ollama-specific model switching behavior."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import surfaces.shared.llm_setup.ollama_models as ollama
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


def _capture() -> tuple[Console, io.StringIO]:
    output = io.StringIO()
    return Console(file=output, force_terminal=False, highlight=False), output


def test_model_menu_choices_come_from_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama, "list_available_models", lambda _: ("llama3.2:latest", "qwen2.5:7b")
    )
    assert ollama.discover_models(PROVIDER_BY_VALUE["ollama"]) == (
        ("llama3.2:latest", "llama3.2:latest"),
        ("qwen2.5:7b", "qwen2.5:7b"),
    )


def test_unavailable_model_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(ollama, "list_available_models", lambda _: ("llama3.2:latest",))
    with pytest.raises(ValueError, match="Ollama model is not available: ghost-model"):
        ollama.validate_model(PROVIDER_BY_VALUE["ollama"], "ghost-model")


def test_model_detection_can_be_cancelled(monkeypatch) -> None:
    def cancel(_host):
        raise KeyboardInterrupt

    monkeypatch.setattr(ollama, "list_available_models", cancel)
    with pytest.raises(KeyboardInterrupt):
        ollama.discover_models(PROVIDER_BY_VALUE["ollama"])
