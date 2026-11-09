"""New Relic NerdGraph (GraphQL) client for alert and metric evidence.

:meth:`NewRelicClient.run_nrql` is the only place that speaks NRQL-over-
GraphQL; both the alerts tool and the metrics tool (Phase 3) go through it so
the transport, the mandatory HTTP-then-errors-then-data checking order, and
the timeout-vs-empty distinction (NFR-7) are implemented exactly once.

Every response is checked in this fixed order, because NerdGraph can return
HTTP 200 with a populated ``errors`` field on query/partial-authorization
failure:

1. HTTP status class (401/403/429/5xx -> structured error by class).
2. ``payload["errors"]`` non-empty -> structured error with a sanitized
   vendor message (the NRQL text itself is never echoed back, since it may
   embed account-specific literals).
3. Only then is ``data.actor.account.nrql.results`` navigated.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

import httpx

from config.constants.new_relic import (
    NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
    NEW_RELIC_DEFAULT_WINDOW_MINUTES,
    NEW_RELIC_NRQL_LIMIT_MAX,
    NEW_RELIC_NRQL_TIMEOUT_SECONDS,
)
from infrastructure.observability.errors.service import capture_service_error
from integrations.new_relic.config import NewRelicIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

#: NerdGraph path for every request this client makes.
_GRAPHQL_PATH = "/graphql"

# error_type values returned in the structured error dict. NFR-7 requires
# "timeout" to be distinct from an empty-but-successful result, so it gets its
# own value rather than folding into a generic "graphql_error".
_ERROR_TYPE_NOT_CONFIGURED = "not_configured"
_ERROR_TYPE_INVALID_ACCOUNT_ID = "invalid_account_id"
_ERROR_TYPE_TIMEOUT = "timeout"
_ERROR_TYPE_UNREACHABLE = "unreachable"
_ERROR_TYPE_AUTH = "auth_error"
_ERROR_TYPE_RATE_LIMITED = "rate_limited"
_ERROR_TYPE_SERVER_ERROR = "server_error"
_ERROR_TYPE_HTTP = "http_error"
_ERROR_TYPE_GRAPHQL = "graphql_error"
_ERROR_TYPE_INVALID_RESPONSE = "invalid_response"
_ERROR_TYPE_ACCOUNT_UNREACHABLE = "account_unreachable"

# Keywords used to recognize a vendor-reported query timeout inside a GraphQL
# ``errors`` entry, since NerdGraph surfaces the 5s NRQL-via-API timeout as a
# normal (HTTP 200) GraphQL error rather than a distinct HTTP status.
_TIMEOUT_MESSAGE_HINTS = ("timeout", "timed out", "took too long")

#: Query used by both :meth:`NewRelicClient.query_incidents` and
#: :meth:`NewRelicClient.query_metrics`, via :meth:`NewRelicClient.run_nrql`.
_NRQL_GRAPHQL_QUERY = (
    "query($accountId: Int!, $nrql: Nrql!) { "
    "actor { account(id: $accountId) { nrql(query: $nrql) { results } } } }"
)

#: Identity + account-access probe used by :meth:`NewRelicClient.probe_access`
#: (FR-4). Combines both checks in one request and distinguishes them by
#: which part of the response comes back empty.
_PROBE_GRAPHQL_QUERY = (
    "query($accountId: Int!) { actor { user { name } account(id: $accountId) { name } } }"
)

_INCIDENTS_SELECT = (
    "SELECT incidentId, event, priority, title, conditionName, policyName, "
    "nrqlQuery, threshold, operator, runbookUrl, entity.name, entity.guid, "
    "muted, openTime, closeTime FROM NrAiIncident"
)


def _error(message: str, *, error_type: str) -> dict[str, Any]:
    """Build the structured error shape shared with Honeycomb/Datadog clients."""
    return {"success": False, "error": message, "error_type": error_type}


def _classify_http_status(status_code: int) -> str:
    if status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return _ERROR_TYPE_AUTH
    if status_code == HTTPStatus.TOO_MANY_REQUESTS:
        return _ERROR_TYPE_RATE_LIMITED
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return _ERROR_TYPE_SERVER_ERROR
    return _ERROR_TYPE_HTTP


def _looks_like_timeout(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _TIMEOUT_MESSAGE_HINTS)


def _nrql_string_literal(value: str) -> str:
    """Quote *value* as an NRQL string literal, escaping backslash and quote."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _dig(payload: object, *keys: str) -> Any:
    """Navigate nested ``dict`` keys, returning ``None`` on any missing hop."""
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


class NewRelicClient:
    """Synchronous NerdGraph client for read-only alert and metric queries."""

    def __init__(self, config: NewRelicIntegrationConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers={
                    "API-Key": self.config.api_key,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key and self.config.account_id)

    def _account_id_as_int(self) -> tuple[int | None, dict[str, Any] | None]:
        try:
            return int(self.config.account_id), None
        except ValueError:
            return None, _error(
                f"New Relic account_id must be numeric, got {self.config.account_id!r}.",
                error_type=_ERROR_TYPE_INVALID_ACCOUNT_ID,
            )

    def _request_graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        """POST one GraphQL request and apply the mandatory checking order.

        Returns ``{"success": True, "payload": <decoded body>}`` or the
        structured error shape — this method never raises.
        """
        try:
            response = self._get_client().post(
                _GRAPHQL_PATH,
                json={"query": query, "variables": variables},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            return _error(
                f"New Relic query timed out after {timeout}s — narrow the time window or query.",
                error_type=_ERROR_TYPE_TIMEOUT,
            )
        except httpx.RequestError as exc:
            capture_service_error(exc, logger=logger, integration="new_relic", method="request")
            return _error(
                f"Could not reach New Relic: {type(exc).__name__}.",
                error_type=_ERROR_TYPE_UNREACHABLE,
            )
        except Exception as exc:  # pragma: no cover - defensive, never expected
            capture_service_error(exc, logger=logger, integration="new_relic", method="request")
            return _error(
                "Unexpected error while calling New Relic.",
                error_type=_ERROR_TYPE_UNREACHABLE,
            )

        if response.status_code != HTTPStatus.OK:
            return _error(
                f"New Relic API returned HTTP {response.status_code}.",
                error_type=_classify_http_status(response.status_code),
            )

        try:
            payload = response.json()
        except ValueError:
            return _error(
                "New Relic returned a non-JSON response.",
                error_type=_ERROR_TYPE_INVALID_RESPONSE,
            )

        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            messages = [str(item.get("message", "")) for item in errors if isinstance(item, dict)]
            combined = " ".join(message for message in messages if message)
            error_type = (
                _ERROR_TYPE_TIMEOUT if _looks_like_timeout(combined) else (_ERROR_TYPE_GRAPHQL)
            )
            detail = combined[:300] or "unspecified GraphQL error"
            return _error(
                f"New Relic API error: {detail}",
                error_type=error_type,
            )

        return {"success": True, "payload": payload}

    def probe_access(self) -> ProbeResult:
        """Validate the API key and account access in a single call (FR-4).

        Distinguishes an invalid/unauthenticated key (HTTP 401/403, or a
        missing ``actor.user``) from a valid key that cannot reach the given
        account (``actor.account`` comes back ``null``).
        """
        if not self.is_configured:
            return ProbeResult.missing("Missing New Relic API key or account ID.")

        account_id, id_error = self._account_id_as_int()
        if id_error is not None:
            return ProbeResult.failed(str(id_error["error"]))

        outcome = self._request_graphql(
            _PROBE_GRAPHQL_QUERY,
            {"accountId": account_id},
            timeout=NEW_RELIC_NRQL_TIMEOUT_SECONDS,
        )
        if not outcome["success"]:
            return ProbeResult.failed(str(outcome["error"]))

        actor = _dig(outcome["payload"], "data", "actor")
        user = actor.get("user") if isinstance(actor, dict) else None
        account = actor.get("account") if isinstance(actor, dict) else None

        if not isinstance(user, dict) or not user.get("name"):
            return ProbeResult.failed("New Relic API key did not resolve to a valid user.")
        if not isinstance(account, dict) or not account.get("name"):
            return ProbeResult.failed(
                f"New Relic API key is valid but account {self.config.account_id} "
                "is not accessible."
            )

        return ProbeResult.passed(
            f"Connected to {self.config.base_url} as {user['name']}; "
            f"account {self.config.account_id} is accessible.",
            account_id=self.config.account_id,
        )

    def run_nrql(
        self,
        nrql: str,
        *,
        timeout: float = NEW_RELIC_NRQL_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Run *nrql* through NerdGraph and return its raw ``results`` rows.

        The only method that speaks GraphQL for data queries — the alerts and
        metrics tools (Phase 3) both call this, never the transport directly.
        """
        if not self.is_configured:
            return _error(
                "Missing New Relic API key or account ID.",
                error_type=_ERROR_TYPE_NOT_CONFIGURED,
            )

        account_id, id_error = self._account_id_as_int()
        if id_error is not None:
            return id_error

        outcome = self._request_graphql(
            _NRQL_GRAPHQL_QUERY,
            {"accountId": account_id, "nrql": nrql},
            timeout=timeout,
        )
        if not outcome["success"]:
            return outcome

        account = _dig(outcome["payload"], "data", "actor", "account")
        if account is None:
            return _error(
                f"New Relic account {self.config.account_id} is not accessible with this API key.",
                error_type=_ERROR_TYPE_ACCOUNT_UNREACHABLE,
            )

        results = _dig(account, "nrql", "results")
        if not isinstance(results, list):
            return _error(
                "New Relic returned an unexpected NRQL response shape.",
                error_type=_ERROR_TYPE_INVALID_RESPONSE,
            )

        return {"success": True, "results": results}

    def query_incidents(
        self,
        *,
        since_minutes: int = NEW_RELIC_DEFAULT_WINDOW_MINUTES,
        priority: str | None = None,
        entity_name: str | None = None,
        limit: int = NEW_RELIC_DEFAULT_INCIDENT_LIMIT,
    ) -> dict[str, Any]:
        """Run the ``NrAiIncident`` query backing the alerts tool.

        Returns the raw, unpaired transition rows — pairing rows by
        ``incidentId`` into one record per incident, decoding HTML entities,
        and flagging ``muted`` is the alerts tool's job, not the client's, so
        the same rows can also be inspected untouched in tests.
        """
        capped_limit = min(max(int(limit), 1), NEW_RELIC_NRQL_LIMIT_MAX)
        clauses: list[str] = []
        if priority:
            clauses.append(f"priority = {_nrql_string_literal(priority)}")
        if entity_name:
            clauses.append(f"entity.name = {_nrql_string_literal(entity_name)}")
        where_clause = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # ORDER BY timestamp DESC is explicit, not incidental: a resolved
        # incident's close row is always chronologically >= its open row, so
        # newest-first ordering guarantees the close row (which alone already
        # carries both openTime and closeTime) is never the one a LIMIT
        # cutoff drops. Relying on the vendor's undocumented default order
        # for this would silently break — a truncated fetch could otherwise
        # keep an incident's open row and drop its close row, reporting a
        # resolved incident as still open.
        nrql = (
            f"{_INCIDENTS_SELECT}{where_clause} "
            f"SINCE {int(since_minutes)} minutes ago ORDER BY timestamp DESC LIMIT {capped_limit}"
        )
        outcome = self.run_nrql(nrql)
        # Callers detect truncation by comparing the raw row count against the
        # limit that was actually executed — not the caller's original ask,
        # which this method may have clamped down to the vendor's ceiling.
        outcome["effective_limit"] = capped_limit
        return outcome

    def query_metrics(
        self,
        *,
        nrql: str,
        timeout: float = NEW_RELIC_NRQL_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Run an already-composed NRQL metrics query through :meth:`run_nrql`.

        Injecting a default ``SINCE``/``LIMIT`` when the caller's NRQL omits
        them is the metrics tool's ``validation.py`` responsibility (Phase 3):
        keeping that logic out of the client keeps ``run_nrql`` the single
        GraphQL-speaking place, with no query-construction duplicated here.
        """
        return self.run_nrql(nrql, timeout=timeout)
