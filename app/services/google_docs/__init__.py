"""Google Docs integration client for creating incident reports."""

from __future__ import annotations

from app.services.google_docs.client import (
    GoogleDocsClient,
    build_google_docs_client_from_env,
)

__all__ = ["GoogleDocsClient", "build_google_docs_client_from_env"]
