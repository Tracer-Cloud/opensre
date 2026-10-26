"""Ollama model discovery and tag rules."""

from __future__ import annotations

from typing import Any

import httpx

_MODEL_LIST_TIMEOUT_SECONDS = 5.0


class OllamaModelDiscoveryError(RuntimeError):
    """Raised when the configured Ollama host cannot provide its model catalog."""


def normalize_model_tag(model: str) -> str:
    """Append ``:latest`` when the tag is missing, matching Ollama's own default."""
    return model if ":" in model else f"{model}:latest"


def list_available_models(host: str) -> tuple[str, ...]:
    """Return model names exposed by the configured Ollama host."""
    try:
        response = httpx.get(
            f"{host.rstrip('/')}/api/tags",
            timeout=_MODEL_LIST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload: Any = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OllamaModelDiscoveryError(f"Cannot reach Ollama at {host}: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise OllamaModelDiscoveryError(f"Ollama at {host} returned an invalid model catalog.")

    names = {
        str(item.get("name") or item.get("model") or "").strip()
        for item in payload["models"]
        if isinstance(item, dict)
    }
    return tuple(sorted(name for name in names if name))


def model_is_available(model: str, available_models: tuple[str, ...]) -> bool:
    """Return whether ``model`` resolves to an available Ollama tag."""
    normalized_available = {normalize_model_tag(candidate) for candidate in available_models}
    if normalize_model_tag(model) in normalized_available:
        return True
    if ":" in model:
        return False
    return any(candidate.split(":", maxsplit=1)[0] == model for candidate in available_models)


__all__ = [
    "OllamaModelDiscoveryError",
    "list_available_models",
    "model_is_available",
    "normalize_model_tag",
]
