"""Tests for the local_llm module (hardware detection, ollama lifecycle, validation)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.cli.local_llm.hardware import HardwareProfile, recommend_model
from app.cli.local_llm.ollama import is_model_present, pull_model
from app.cli.wizard.config import PROVIDER_BY_VALUE
from app.cli.wizard.validation import validate_provider_credentials

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_request(url: str = "http://localhost:11434") -> httpx.Request:
    return httpx.Request("GET", url)


def _tags_response(models: list[str]) -> httpx.Response:
    body = {"models": [{"name": m} for m in models]}
    return httpx.Response(200, json=body, request=_fake_request("/api/tags"))


def _chat_response(content: str) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}]}
    return httpx.Response(200, json=body, request=_fake_request("/v1/chat/completions"))


# ---------------------------------------------------------------------------
# recommend_model — hardware-based model selection
# ---------------------------------------------------------------------------


def _hw(**kwargs) -> HardwareProfile:
    defaults = {
        "total_ram_gb": 8.0,
        "available_ram_gb": 4.0,
        "arch": "arm64",
        "is_apple_silicon": False,
        "has_nvidia_gpu": False,
    }
    return HardwareProfile(**{**defaults, **kwargs})


def test_recommend_model_apple_silicon_16gb_returns_8b() -> None:
    hw = _hw(total_ram_gb=16.0, available_ram_gb=12.0, is_apple_silicon=True)
    model, reason = recommend_model(hw)
    assert model == "llama3.1:8b"
    assert "Apple Silicon" in reason


def test_recommend_model_nvidia_gpu_returns_8b() -> None:
    hw = _hw(total_ram_gb=8.0, available_ram_gb=6.0, has_nvidia_gpu=True)
    model, _ = recommend_model(hw)
    assert model == "llama3.1:8b"


def test_recommend_model_12gb_safe_ram_returns_8b() -> None:
    # safe_ram = min(available=24, total*0.5=12) = 12 → 8b threshold met
    hw = _hw(total_ram_gb=24.0, available_ram_gb=24.0)
    model, _ = recommend_model(hw)
    assert model == "llama3.1:8b"


def test_recommend_model_low_ram_returns_3b() -> None:
    # safe_ram = min(available=4, total*0.5=4) = 4 → below 12GB threshold
    hw = _hw(total_ram_gb=8.0, available_ram_gb=4.0)
    model, reason = recommend_model(hw)
    assert model == "llama3.2"
    assert "3B" in reason


def test_recommend_model_apple_silicon_8gb_returns_3b() -> None:
    # Apple Silicon but < 16GB → falls through to safe_ram check
    hw = _hw(total_ram_gb=8.0, available_ram_gb=6.0, is_apple_silicon=True)
    model, _ = recommend_model(hw)
    assert model == "llama3.2"


def test_recommend_model_apple_silicon_16gb_low_free_ram_returns_3b() -> None:
    # 16GB Apple Silicon but only 3GB free (heavy workload) → falls through to safe_ram check
    hw = _hw(total_ram_gb=16.0, available_ram_gb=3.0, is_apple_silicon=True)
    model, _ = recommend_model(hw)
    assert model == "llama3.2"


# ---------------------------------------------------------------------------
# is_model_present — exact tag matching
# ---------------------------------------------------------------------------


def test_is_model_present_returns_true_for_exact_tag(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_kw: _tags_response(["llama3.1:8b", "llama3.2:latest"])
    )
    assert is_model_present("llama3.1:8b") is True


def test_is_model_present_returns_false_for_different_tag(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _tags_response(["llama3.1:latest"]))
    assert is_model_present("llama3.1:8b") is False


def test_is_model_present_returns_false_on_connection_error(monkeypatch) -> None:
    def _raise(*_a, **_kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _raise)
    assert is_model_present("llama3.1:8b") is False


# ---------------------------------------------------------------------------
# pull_model — skips download when model already present
# ---------------------------------------------------------------------------


def test_pull_model_skips_download_if_already_present(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.is_model_present", lambda *_a, **_kw: True)
    run_called = []
    monkeypatch.setattr(
        "app.cli.local_llm.ollama.subprocess.run", lambda *_a, **_kw: run_called.append(True)
    )
    console = MagicMock()
    result = pull_model("llama3.1:8b", console)
    assert result is True
    assert not run_called


def test_pull_model_runs_ollama_pull_when_not_present(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.is_model_present", lambda *_a, **_kw: False)
    fake_result = MagicMock()
    fake_result.returncode = 0
    monkeypatch.setattr("app.cli.local_llm.ollama.subprocess.run", lambda *_a, **_kw: fake_result)
    console = MagicMock()
    console.status.return_value.__enter__ = lambda s: s
    console.status.return_value.__exit__ = MagicMock(return_value=False)
    result = pull_model("llama3.1:8b", console)
    assert result is True


# ---------------------------------------------------------------------------
# _check_ollama / validate_provider_credentials — Ollama path
# ---------------------------------------------------------------------------


def test_validate_ollama_returns_failure_when_server_unreachable(monkeypatch) -> None:
    def _raise(*_a, **_kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", _raise)

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["ollama"],
        api_key="http://localhost:11434",
        model="llama3.2",
    )

    assert result.ok is False
    assert "Cannot reach Ollama" in result.detail


def test_validate_ollama_returns_failure_when_model_not_pulled(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _tags_response(["llama3.2:latest"]))

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["ollama"],
        api_key="http://localhost:11434",
        model="llama3.1:8b",
    )

    assert result.ok is False
    assert "llama3.1:8b" in result.detail


def test_validate_ollama_returns_failure_when_inference_fails(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _tags_response(["llama3.2"]))

    def _raise(*_a, **_kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["ollama"],
        api_key="http://localhost:11434",
        model="llama3.2",
    )

    assert result.ok is False
    assert "failed to respond" in result.detail


def test_validate_ollama_returns_success_on_valid_inference(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _tags_response(["llama3.2"]))
    monkeypatch.setattr(httpx, "post", lambda *_a, **_kw: _chat_response("OpenSRE ready"))

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["ollama"],
        api_key="http://localhost:11434",
        model="llama3.2",
    )

    assert result.ok is True
    assert result.sample_response == "OpenSRE ready"


@pytest.mark.parametrize(
    "model,available,should_pass",
    [
        ("llama3.1:8b", ["llama3.1:8b"], True),  # exact match
        ("llama3.1:8b", ["llama3.1:latest"], False),  # different tag — must fail
        ("llama3.2", ["llama3.2"], True),  # no tag — exact match
    ],
)
def test_validate_ollama_exact_tag_matching(monkeypatch, model, available, should_pass) -> None:
    monkeypatch.setattr(httpx, "get", lambda *_a, **_kw: _tags_response(available))
    if should_pass:
        monkeypatch.setattr(httpx, "post", lambda *_a, **_kw: _chat_response("OpenSRE ready"))

    result = validate_provider_credentials(
        provider=PROVIDER_BY_VALUE["ollama"],
        api_key="http://localhost:11434",
        model=model,
    )

    assert result.ok is should_pass
