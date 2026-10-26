"""Google Vertex AI provider helpers for LiteLLM routing.

Vertex AI authenticates via Google Application Default Credentials (ADC) —
``gcloud auth application-default login``, ``GOOGLE_APPLICATION_CREDENTIALS``
pointing to a service-account key, or the GCE/GKE metadata server. OpenSRE
never stores a Vertex secret; it only carries the non-secret project and
location that LiteLLM needs to build the Vertex request URL.
"""

from __future__ import annotations

import os
from typing import Any

from core.llm.types import ModelType

VERTEX_AI_PROVIDER = "vertex-ai"

VERTEX_AI_PROJECT_ENV = "VERTEX_AI_PROJECT"
VERTEX_AI_LOCATION_ENV = "VERTEX_AI_LOCATION"


def is_vertex_ai_provider(provider: str) -> bool:
    """Return whether *provider* is the Vertex AI LLM slug."""
    return provider.strip().lower() == VERTEX_AI_PROVIDER


def resolve_vertex_ai_location(value: str = "") -> str:
    """Return the configured Vertex AI region, falling back to the OpenSRE default."""
    from config.llm_models import DEFAULT_VERTEX_AI_LOCATION

    location = (value or os.getenv(VERTEX_AI_LOCATION_ENV, "")).strip()
    return location or DEFAULT_VERTEX_AI_LOCATION


def select_vertex_ai_model(settings: Any, model_type: ModelType) -> str:
    """Return the configured Vertex AI model for *model_type*."""
    attr = f"vertex_ai_{model_type}_model"
    return str(getattr(settings, attr))


def resolve_vertex_ai_request_kwargs(settings: Any, *, model_type: ModelType) -> dict[str, str]:
    """Resolve LiteLLM request fields for Vertex AI from runtime settings.

    ``vertex_project`` is omitted (not raised on) when unset, matching Bedrock's
    ambient-credential precedent: OpenSRE does not enforce a required-field
    precondition here — a missing project surfaces as a LiteLLM/google-auth
    error at request time instead.
    """
    model = select_vertex_ai_model(settings, model_type)
    kwargs: dict[str, str] = {
        "litellm_model": f"vertex_ai/{model}",
        "vertex_location": resolve_vertex_ai_location(
            str(getattr(settings, "vertex_ai_location", ""))
        ),
    }
    project = str(getattr(settings, "vertex_ai_project", "")).strip()
    if project:
        kwargs["vertex_project"] = project
    return kwargs


__all__ = [
    "VERTEX_AI_LOCATION_ENV",
    "VERTEX_AI_PROJECT_ENV",
    "VERTEX_AI_PROVIDER",
    "is_vertex_ai_provider",
    "resolve_vertex_ai_location",
    "resolve_vertex_ai_request_kwargs",
    "select_vertex_ai_model",
]
