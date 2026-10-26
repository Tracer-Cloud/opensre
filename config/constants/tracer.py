"""Tracer environment variable names."""

from __future__ import annotations

TRACER_BASE_URL_ENV = "TRACER_API_URL"

# Built-in Tracer deployments; ``config.tracer_urls`` picks between them by
# environment when ``TRACER_API_URL`` is not set.
TRACER_BASE_URL_DEV = "https://staging.tracer.cloud"
TRACER_BASE_URL_PROD = "https://app.tracer.cloud"
# Mirrors the ``jwt_token`` credential; the name deliberately differs.
TRACER_JWT_TOKEN_ENV = "JWT_TOKEN"

__all__ = [
    "TRACER_BASE_URL_DEV",
    "TRACER_BASE_URL_ENV",
    "TRACER_BASE_URL_PROD",
    "TRACER_JWT_TOKEN_ENV",
]
