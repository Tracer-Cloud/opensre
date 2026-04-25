"""Shared Elasticsearch client factories and helpers for tool actions."""

from __future__ import annotations

from typing import Any

from app.services.elasticsearch import ElasticsearchClient, ElasticsearchConfig


def make_client(
    url: str | None,
    api_key: str | None = None,
    index_pattern: str = "*",
) -> ElasticsearchClient | None:
    if not url:
        return None
    return ElasticsearchClient(
        ElasticsearchConfig(url=url, api_key=api_key or None, index_pattern=index_pattern)
    )


