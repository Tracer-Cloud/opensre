"""Elasticsearch REST API client module."""

from app.services.elasticsearch.client import ElasticsearchClient, ElasticsearchConfig

__all__ = ["ElasticsearchClient", "ElasticsearchConfig"]
