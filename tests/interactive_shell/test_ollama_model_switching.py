"""Tests for Ollama-specific model switching behavior."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.command_registry.model import ollama
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


def _capture() -> tuple[Console, io.StringIO]:
    output = io.StringIO()
    return Console(file=output, force_terminal=False, highlight=False), output


def test_model_menu_choices_come_from_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama,
        "list_available_models",
        lambda _host: ("llama3.2:latest", "qwen2.5:7b"),
    )
    console, _ = _capture()

    assert ollama.model_menu_choices(PROVIDER_BY_VALUE["ollama"], console) == [
        ("llama3.2:latest", "llama3.2:latest"),
        ("qwen2.5:7b", "qwen2.5:7b"),
    ]


def test_unavailable_model_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama,
        "list_available_models",
        lambda _host: ("llama3.2:latest",),
    )
    console, output = _capture()

    assert (
        ollama.validate_model_available(PROVIDER_BY_VALUE["ollama"], "ghost-model", console)
        is False
    )
    assert "Ollama model is not available: ghost-model" in output.getvalue()


def test_model_detection_can_be_cancelled(monkeypatch) -> None:
    def _cancel(_host: str) -> tuple[str, ...]:
        raise KeyboardInterrupt

    monkeypatch.setattr(ollama, "list_available_models", _cancel)
    console, output = _capture()

    assert ollama.model_menu_choices(PROVIDER_BY_VALUE["ollama"], console) is None
    assert "Ollama model detection cancelled" in output.getvalue()
