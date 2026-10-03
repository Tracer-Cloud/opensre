"""What the OpenSearch/Elasticsearch search tools return when a search cannot run.

Both tools share the ``opensearch`` integration and its client. A failure that only
fixing that setup resolves (no URL, rejected credentials, a user without read access)
carries the setup command and one line for the user, so the turn ends with guidance
instead of a retry. A 404
is an index pattern that names no index, usually one the model chose (a wildcard
that matches nothing answers 200 with no hits), so it stays an ordinary error that
says how to retry. Timeouts, refused connections and 5xx answers pass on their own
and stay plain errors.
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
#: For the model only: how a setup-required result ends the turn.
_RUN_SETUP = (
    f"tell the user to run `{OPENSEARCH_INTEGRATION_SETUP_SLASH}` "
    f"(`{OPENSEARCH_INTEGRATION_SETUP_CLI}` from a terminal) and end the turn; another "
    "call with this configuration fails the same way."
)
#: For the model only: a refused read needs an access grant or other credentials.
_GRANT_OR_RUN_SETUP = (
    "tell the user to grant the configured user read access, or to run "
    f"`{OPENSEARCH_INTEGRATION_SETUP_SLASH}` (`{OPENSEARCH_INTEGRATION_SETUP_CLI}` from a "
    "terminal) with credentials that have it, and end the turn; another call with this "
    "configuration fails the same way."
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

    Branches on the client's ``status_code``. A 404 tells the model how to retry with
    another index pattern. A search that matches nothing is a success and never
    reaches here.
    """
    detail = str(result.get("error") or f"Unknown {vendor} error.")
    status = result.get("status_code")
    if status == HTTPStatus.UNAUTHORIZED:
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
    if status == HTTPStatus.FORBIDDEN:
        return _setup_required(
            source,
            error=(
                f"{vendor} refused the search: the configured user may not read the requested "
                f"index pattern ({detail}). If you chose index_pattern yourself, call again "
                f"without it to search the configured pattern. Otherwise {_GRANT_OR_RUN_SETUP}"
            ),
            response_text=(
                f"The configured {vendor} user isn't allowed to read this index. Grant it read "
                "access, or re-run setup with credentials that have it "
                f"(`{OPENSEARCH_INTEGRATION_SETUP_CLI}`)."
            ),
        )
    if status == HTTPStatus.NOT_FOUND:
        error = (
            f"{vendor} found no index for the requested index pattern ({detail}). Call again "
            "without index_pattern to search the configured pattern, or with a wildcard such "
            "as `logs-*`; a wildcard that matches no index returns no hits instead of this "
            "error. If the configured pattern gets this answer too, the configured URL or "
            "index pattern is wrong."
        )
        return unavailable(source, _EMPTY_KEY, error)
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
