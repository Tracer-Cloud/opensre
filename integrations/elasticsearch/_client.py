"""Shared Elasticsearch client factories and helpers for tool actions."""

from __future__ import annotations

from typing import Any

from core.tool_framework.utils import tool_unavailable
from integrations.elasticsearch.client import ElasticsearchClient, ElasticsearchConfig


def make_client(
    url: str | None,
    api_key: str | None = None,
    username: str | None = None,
    password: str | None = None,
    index_pattern: str = "*",
) -> ElasticsearchClient | None:
    if not url:
        return None
    return ElasticsearchClient(
        ElasticsearchConfig(
            url=url,
            api_key=api_key or None,
            username=username or None,
            password=password or None,
            index_pattern=index_pattern,
        )
    )


def unavailable(source: str, empty_key: str, error: str, **extra: Any) -> dict[str, Any]:
    """Standardised unavailable response — mirrors the Datadog helper."""
    return tool_unavailable(source, error, **{empty_key: [], **extra})
