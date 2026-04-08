"""Confluence API client for incident runbook and operational document search."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from app.integrations.models import ConfluenceIntegrationConfig

ConfluenceConfig = ConfluenceIntegrationConfig

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_DEFAULT_CONFLUENCE_BASE_URL = ""
_CONFLUENCE_API_PATH = "/wiki/rest/api/content"
_SEARCH_LABELS = (
    "runbook",
    "runbooks",
    "playbook",
    "playbooks",
    "incident",
    "ops",
    "operations",
    "operational",
    "documentation",
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "for",
    "from",
    "had",
    "has",
    "have",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "out",
    "over",
    "please",
    "should",
    "that",
    "the",
    "their",
    "this",
    "to",
    "under",
    "was",
    "were",
    "when",
    "where",
    "which",
    "with",
    "your",
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ConfluenceValidationResult:
    """Result of validating a Confluence integration."""

    ok: bool
    detail: str


def build_confluence_config(raw: dict[str, Any] | None) -> ConfluenceConfig:
    """Build a normalized Confluence config object from env/store data."""
    return ConfluenceConfig.model_validate(raw or {})


def confluence_config_from_env() -> ConfluenceConfig | None:
    """Load a Confluence config from env vars."""
    base_url = os.getenv("CONFLUENCE_BASE_URL", _DEFAULT_CONFLUENCE_BASE_URL).strip()
    email = os.getenv("CONFLUENCE_EMAIL", "").strip()
    api_token = os.getenv("CONFLUENCE_API_TOKEN", "").strip()
    if not (base_url and email and api_token):
        return None
    return build_confluence_config(
        {
            "base_url": base_url,
            "email": email,
            "api_token": api_token,
            "space_key": os.getenv("CONFLUENCE_SPACE_KEY", "").strip(),
        }
    )


def _get_client(config: ConfluenceConfig) -> httpx.Client:
    """Create an authenticated httpx client for Confluence API calls."""
    return httpx.Client(
        base_url=config.base_url,
        auth=(config.email, config.api_token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=_DEFAULT_TIMEOUT,
    )


def validate_confluence_config(config: ConfluenceConfig) -> ConfluenceValidationResult:
    """Validate Confluence connectivity with a cheap CQL search request."""
    if not (config.base_url and config.email and config.api_token):
        return ConfluenceValidationResult(
            ok=False,
            detail="Confluence base_url, email, and api_token are all required.",
        )

    client = _get_client(config)
    try:
        resp = client.get(f"{_CONFLUENCE_API_PATH}/search", params={"cql": "type = page", "limit": 1})
        resp.raise_for_status()
        return ConfluenceValidationResult(
            ok=True,
            detail=f"Authenticated; space: {config.space_key or 'all'}.",
        )
    except Exception as err:  # noqa: BLE001
        return ConfluenceValidationResult(ok=False, detail=f"Confluence connection failed: {err}")
    finally:
        client.close()


class ConfluenceClient:
    """Synchronous client for Confluence Cloud search."""

    def __init__(self, config: ConfluenceIntegrationConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = _get_client(self.config)
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> ConfluenceClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_configured(self) -> bool:
        return bool(self.config.base_url and self.config.email and self.config.api_token)

    def search_relevant_documents(
        self,
        query: str,
        limit: int = 20,
        space_key: str | None = None,
        cursor: str | None = None,
        include_archived_spaces: bool = False,
    ) -> dict[str, Any]:
        """Search Confluence for runbooks, docs, and operational pages relevant to an incident.

        Args:
            query: Free-form incident/crash description to search against CQL text and titles.
            limit: Maximum number of results to return.
            space_key: Optional Confluence space key to scope the search.
            cursor: Optional pagination cursor from a prior response.
            include_archived_spaces: Whether archived spaces should be included.
        """
        cql = self._build_cql(query=query, space_key=space_key)
        params: dict[str, Any] = {
            "cql": cql,
            "limit": min(max(limit, 1), 25),
            "includeArchivedSpaces": str(bool(include_archived_spaces)).lower(),
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = self._get_client().get(f"{_CONFLUENCE_API_PATH}/search", params=params)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
            raw_results = payload.get("results", [])
            results = [self._normalize_result(item) for item in raw_results if isinstance(item, dict)]
            links = payload.get("_links", {})
            return {
                "success": True,
                "results": results,
                "total": payload.get("totalSize", len(results)),
                "size": payload.get("size", len(results)),
                "limit": payload.get("limit", params["limit"]),
                "query": query,
                "cql": cql,
                "space_key": space_key or self.config.space_key or "",
                "cursor": cursor,
                "next_cursor": self._extract_cursor(links.get("next", "")),
                "prev_cursor": self._extract_cursor(links.get("prev", "")),
            }
        except httpx.HTTPStatusError as e:
            logger.warning(
                "[confluence] search_relevant_documents HTTP failure status=%s query=%r",
                e.response.status_code,
                query,
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.warning("[confluence] search_relevant_documents error type=%s detail=%s", type(e).__name__, e)
            return {"success": False, "error": str(e)}

    def _build_cql(self, query: str, space_key: str | None = None) -> str:
        clauses: list[str] = ["type = page"]

        effective_space = (space_key or self.config.space_key or "").strip()
        if effective_space:
            clauses.append(f'space = "{self._escape_cql_value(effective_space)}"')

        doc_bias = self._build_document_bias_clause()
        search_clause = self._build_query_clause(query)

        if search_clause:
            clauses.append(f"({doc_bias} AND {search_clause})")
        else:
            clauses.append(doc_bias)

        return " AND ".join(clauses)

    def _build_document_bias_clause(self) -> str:
        label_clause = "label in (" + ", ".join(f'"{label}"' for label in _SEARCH_LABELS) + ")"
        title_clause = " OR ".join(
            [
                'title ~ "runbook"',
                'title ~ "playbook"',
                'title ~ "incident"',
                'title ~ "operational"',
                'title ~ "ops"',
                'text ~ "runbook"',
                'text ~ "playbook"',
                'text ~ "incident"',
                'text ~ "operational"',
                'text ~ "ops"',
                'text ~ "postmortem"',
            ]
        )
        return f"({label_clause} OR {title_clause})"

    def _build_query_clause(self, query: str) -> str:
        cleaned = " ".join(query.split()).strip()
        if not cleaned:
            return ""

        tokens = self._extract_terms(cleaned)
        phrases = [cleaned] + tokens[:6]
        clauses = []
        for phrase in phrases:
            escaped = self._escape_cql_value(phrase)
            clauses.append(f'text ~ "{escaped}"')
            clauses.append(f'title ~ "{escaped}"')
        return "(" + " OR ".join(dict.fromkeys(clauses)) + ")"

    def _extract_terms(self, query: str) -> list[str]:
        terms: list[str] = []
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", query.lower()):
            if len(token) < 3 or token in _STOPWORDS:
                continue
            if token not in terms:
                terms.append(token)
        return terms

    def _normalize_result(self, item: dict[str, Any]) -> dict[str, Any]:
        content = _as_dict(item.get("content"))
        space = _as_dict(item.get("space"))
        content_space = _as_dict(content.get("space"))
        version = _as_dict(content.get("version"))
        links = _as_dict(item.get("_links"))
        content_links = _as_dict(content.get("_links"))
        metadata = _as_dict(content.get("metadata"))
        labels = _as_dict(metadata.get("labels"))
        label_results = labels.get("results", [])
        if not isinstance(label_results, list):
            label_results = []

        raw_url = item.get("url") or content_links.get("webui") or links.get("webui") or ""
        url = ""
        if raw_url:
            url = raw_url if raw_url.startswith("http") else urljoin(self.config.base_url.rstrip("/"), raw_url)

        return {
            "id": str(item.get("id") or content.get("id") or ""),
            "title": str(item.get("title") or content.get("title") or ""),
            "excerpt": str(item.get("excerpt") or ""),
            "url": url,
            "entity_type": str(item.get("entityType") or content.get("type") or ""),
            "score": item.get("score"),
            "space_key": str(
                item.get("space", {}).get("key")
                if isinstance(item.get("space"), dict)
                else content_space.get("key", "")
                or space.get("key", "")
            ),
            "space_name": str(
                item.get("space", {}).get("name")
                if isinstance(item.get("space"), dict)
                else content_space.get("name", "")
                or space.get("name", "")
            ),
            "last_modified": str(item.get("lastModified") or version.get("when") or ""),
            "labels": [str(label.get("name") or label.get("label") or "") for label in label_results],
        }

    def _extract_cursor(self, link: str) -> str:
        if not link:
            return ""
        match = re.search(r"[?&]cursor=([^&]+)", link)
        return match.group(1) if match else ""

    def _escape_cql_value(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

def search_relevant_documents(
    config: ConfluenceConfig,
    query: str,
    limit: int = 20,
    space_key: str | None = None,
    cursor: str | None = None,
    include_archived_spaces: bool = False,
) -> dict[str, Any]:
    """Search Confluence for runbooks, docs, and operational pages relevant to an incident."""
    client = ConfluenceClient(config)
    try:
        return client.search_relevant_documents(
            query=query,
            limit=limit,
            space_key=space_key,
            cursor=cursor,
            include_archived_spaces=include_archived_spaces,
        )
    finally:
        client.close()


def make_confluence_client(
    base_url: str | None,
    email: str | None,
    api_token: str | None,
    space_key: str | None = None,
    integration_id: str | None = None,
) -> ConfluenceClient | None:
    """Build a Confluence client when credentials are present and valid."""
    try:
        config = build_confluence_config(
            {
                "base_url": base_url or "",
                "email": email or "",
                "api_token": api_token or "",
                "space_key": space_key or "",
                "integration_id": integration_id or "",
            }
        )
    except Exception as exc:
        logger.warning("[confluence] Invalid config: %s", exc)
        return None

    if not (config.base_url and config.email and config.api_token):
        return None
    return ConfluenceClient(config)
