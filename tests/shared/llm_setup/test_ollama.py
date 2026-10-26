"""Tests for Ollama model discovery and tag matching."""

from __future__ import annotations

from http import HTTPStatus

import httpx

from surfaces.shared.llm_setup.ollama import list_available_models, model_is_available


def test_list_available_models_returns_sorted_unique_names(monkeypatch) -> None:
    request = httpx.Request("GET", "http://localhost:11434/api/tags")
    response = httpx.Response(
        HTTPStatus.OK,
        json={
            "models": [
                {"name": "qwen2.5:7b"},
                {"model": "llama3.2:latest"},
                {"name": "qwen2.5:7b"},
            ]
        },
        request=request,
    )
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: response)

    assert list_available_models("http://localhost:11434") == (
        "llama3.2:latest",
        "qwen2.5:7b",
    )


def test_model_is_available_requires_the_selected_tag() -> None:
    available = ("llama3.2:8b", "qwen2.5:latest")

    assert model_is_available("qwen2.5", available) is True
    assert model_is_available("llama3.2:latest", available) is False
