"""What the OpenSearch/Elasticsearch search tools return when a search cannot run.

Both tools share the ``opensearch`` integration and its client. A failure that only
fixing that setup resolves (no URL, refused credentials, a 404 for the configured
index or endpoint) carries the setup command and one line for the user, so the turn
ends with guidance instead of a retry. Timeouts, refused connections and 5xx answers
pass on their own and stay plain errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from config.constants.opensearch import (
    OPENSEARCH_INTEGRATION_SETUP_CLI,
    OPENSEARCH_INTEGRATION_SETUP_SLASH,
)
from integrations.elasticsearch._client import unavailable

_EMPTY_KEY = "logs"
_REFUSED_CREDENTIALS = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})
#: For the model only: how a setup-required result ends the turn.
_RUN_SETUP = (
    f"tell the user to run `{OPENSEARCH_INTEGRATION_SETUP_SLASH}` "
    f"(`{OPENSEARCH_INTEGRATION_SETUP_CLI}` from a terminal) and end the turn; another "
    "call with this configuration fails the same way."
)


def not_configured(source: str, *, vendor: str) -> dict[str, Any]:
    """The result when no cluster URL is configured."""
    return _setup_required(
        source,
        error=(
            f"{vendor} integration not configured: no cluster URL is set. "
            f"Setup is required: {_RUN_SETUP}"
        ),
        response_text=(
            f"{vendor} is not set up yet. Connect it with `{OPENSEARCH_INTEGRATION_SETUP_CLI}`."
        ),
    )


def search_failed(source: str, result: Mapping[str, Any], *, vendor: str) -> dict[str, Any]:
    """The result for a failed client search: setup guidance when only setup fixes it.

    Branches on the client's ``status_code``. A search that matches nothing is a
    success and never reaches here.
    """
    detail = str(result.get("error") or f"Unknown {vendor} error.")
    status = result.get("status_code")
    if status in _REFUSED_CREDENTIALS:
        return _setup_required(
            source,
            error=(
                f"{vendor} rejected the configured credentials ({detail}). "
                f"Setup is required: {_RUN_SETUP}"
            ),
            response_text=(
                f"{vendor} rejected the configured credentials. "
                f"Re-run setup with `{OPENSEARCH_INTEGRATION_SETUP_CLI}`."
            ),
        )
    if status == HTTPStatus.NOT_FOUND:
        return _setup_required(
            source,
            error=(
                f"{vendor} found no index or search endpoint at the configured URL ({detail}). "
                "If you chose index_pattern yourself, call again without it to search the "
                f"configured pattern; otherwise setup is required: {_RUN_SETUP}"
            ),
            response_text=(
                f"{vendor} found no index or search endpoint at the configured URL. Check the "
                "URL and index pattern, and re-run setup with "
                f"`{OPENSEARCH_INTEGRATION_SETUP_CLI}`."
            ),
        )
    return unavailable(source, _EMPTY_KEY, detail)


def _setup_required(source: str, *, error: str, response_text: str) -> dict[str, Any]:
    return unavailable(
        source,
        _EMPTY_KEY,
        error,
        response_text=response_text,
        setup_command=OPENSEARCH_INTEGRATION_SETUP_SLASH,
    )


__all__ = ["not_configured", "search_failed"]
