"""Shared Better Stack Telemetry integration helpers.

Provides configuration, connectivity validation, and read-only log queries
against Better Stack's ClickHouse SQL over HTTP endpoint (Basic auth). Recent
and historical log rows are UNIONed across ``remote(<source>_logs)`` and
``s3Cluster(primary, <source>_s3) WHERE _row_type = 1`` per Better Stack's
own sample query, so a single call returns both live-stream and archived data.

Credentials are generated in the dashboard under ``Integrations → Connect
ClickHouse HTTP client`` (username + password + a region-specific endpoint
like ``https://eu-nbg-2-connect.betterstackdata.com``). The SQL endpoint does
not expose a documented source-listing query, so the optional
``BETTERSTACK_SOURCES`` env var surfaces user-supplied source IDs to the
planner via ``extract_params``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

DEFAULT_BETTERSTACK_TIMEOUT_S = 15
DEFAULT_BETTERSTACK_MAX_ROWS = 500
_MAX_ALLOWED_ROWS = 10_000
_REQUIRED_CONTENT_TYPE = "text/plain"
_REQUIRED_QUERY_PARAMS = {"output_format_pretty_row_numbers": "0"}
_VALIDATION_PROBE_SQL = "SELECT 1 FORMAT JSONEachRow"
# Source identifiers are ClickHouse bare identifiers (``t{team_id}_{source}``).
# Anything else would land in the FROM clause as SQL injection, so reject
# anything not matching this whitelist.
_SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class BetterStackConfig(StrictConfigModel):
    """Normalized Better Stack SQL Query API connection settings."""

    query_endpoint: str = ""
    username: str = ""
    password: str = ""
    sources: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=DEFAULT_BETTERSTACK_TIMEOUT_S, gt=0)
    max_rows: int = Field(default=DEFAULT_BETTERSTACK_MAX_ROWS, gt=0, le=_MAX_ALLOWED_ROWS)
    integration_id: str = ""

    @field_validator("query_endpoint", mode="before")
    @classmethod
    def _normalize_query_endpoint(cls, value: Any) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("username", mode="before")
    @classmethod
    def _normalize_username(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("password", mode="before")
    @classmethod
    def _normalize_password(cls, value: Any) -> str:
        # ``StrictConfigModel`` already strips strings as a wildcard validator;
        # this step only coerces ``None`` / non-string inputs into ``""``.
        return str(value or "")

    @field_validator("sources", mode="before")
    @classmethod
    def _normalize_sources(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return [p for p in parts if p]
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return []

    @property
    def is_configured(self) -> bool:
        return bool(self.query_endpoint and self.username)


def build_betterstack_config(raw: dict[str, Any] | None) -> BetterStackConfig:
    """Build a normalized Better Stack config object from env/store data."""
    return BetterStackConfig.model_validate(raw or {})


def betterstack_config_from_env() -> BetterStackConfig | None:
    """Load a Better Stack config from ``BETTERSTACK_*`` env vars.

    Returns ``None`` when either the endpoint or username is missing. The
    ``BETTERSTACK_SOURCES`` env var is an optional comma-separated hint list
    surfaced to the planner via :func:`betterstack_extract_params`; it is
    not required for availability.
    """
    endpoint = os.getenv("BETTERSTACK_QUERY_ENDPOINT", "").strip()
    username = os.getenv("BETTERSTACK_USERNAME", "").strip()
    if not endpoint or not username:
        return None
    return build_betterstack_config(
        {
            "query_endpoint": endpoint,
            "username": username,
            "password": os.getenv("BETTERSTACK_PASSWORD", ""),
            "sources": os.getenv("BETTERSTACK_SOURCES", ""),
        }
    )


def betterstack_is_available(sources: dict[str, dict]) -> bool:
    """Check if Better Stack credentials are present AND a source is derivable.

    The investigation executor invokes tools purely via
    ``action.run(**extract_params(...))``; there is no other path to inject a
    source identifier at call time. A betterstack integration with credentials
    but no way to derive ``source`` would always run with ``source=""`` and
    deterministically fail. So availability requires, beyond credentials,
    either (a) a configured ``sources`` hint list or (b) an alert-derived
    ``source_hint`` surfaced by
    :mod:`app.nodes.plan_actions.detect_sources` from the alert annotation.
    """
    bs = sources.get("betterstack", {})
    if not (bs.get("query_endpoint") and bs.get("username")):
        return False
    has_sources = bool(bs.get("sources"))
    has_hint = bool(str(bs.get("source_hint") or "").strip())
    return has_sources or has_hint


def betterstack_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Better Stack credentials and optional source hints for tool calls.

    Returns both the full ``sources`` hint list and a scalar ``source`` (derived
    from the alert annotation, when the planner has populated ``source_hint``
    via :mod:`app.nodes.plan_actions.detect_sources`). The executor invokes tools
    purely via ``action.run(**action.extract_params(...))``; we therefore have
    to surface the alert-derived target as a concrete kwarg here — the planner
    can't inject it at call time.
    """
    bs = sources.get("betterstack", {})
    source_hint = str(bs.get("source_hint", "") or "").strip()
    return {
        "query_endpoint": bs.get("query_endpoint", ""),
        "username": bs.get("username", ""),
        "password": bs.get("password", ""),
        "sources": list(bs.get("sources", []) or []),
        "source": source_hint,
    }


@dataclass(frozen=True)
class BetterStackValidationResult:
    """Outcome of validating a Better Stack integration against the SQL endpoint."""

    ok: bool
    detail: str


def _sql_client(config: BetterStackConfig) -> httpx.Client:
    """Build an authenticated ``httpx.Client`` scoped to the Better Stack SQL API."""
    return httpx.Client(
        auth=(config.username, config.password),
        timeout=float(config.timeout_seconds),
    )


def _post_sql(
    client: httpx.Client,
    endpoint: str,
    query: str,
) -> tuple[httpx.Response | None, str | None]:
    """POST a SQL statement to the Better Stack query endpoint.

    Returns ``(response, None)`` on a transport-level success (any HTTP status),
    or ``(None, error_message)`` on a transport-level failure (DNS, TLS,
    timeout, etc.). Callers are responsible for interpreting non-2xx status
    codes since the error phrasing depends on the operation (probe vs query).
    """
    try:
        response = client.post(
            endpoint,
            params=_REQUIRED_QUERY_PARAMS,
            content=query.encode("utf-8"),
            headers={"Content-Type": _REQUIRED_CONTENT_TYPE},
        )
    except httpx.RequestError as err:
        return None, f"Better Stack request failed: {err}"
    return response, None


def validate_betterstack_config(
    config: BetterStackConfig,
) -> BetterStackValidationResult:
    """Validate Better Stack reachability with a cheap ``SELECT 1`` probe."""
    if not config.is_configured:
        return BetterStackValidationResult(
            ok=False,
            detail="Better Stack query_endpoint and username are required.",
        )

    try:
        with _sql_client(config) as client:
            response, err = _post_sql(client, config.query_endpoint, _VALIDATION_PROBE_SQL)
    except Exception as err:  # noqa: BLE001 — final-resort guard around transport setup
        logger.debug("Better Stack validate_config failed", exc_info=True)
        return BetterStackValidationResult(
            ok=False, detail=f"Better Stack connection failed: {err}"
        )

    if err is not None:
        return BetterStackValidationResult(ok=False, detail=err)
    assert response is not None  # noqa: S101 — narrow for mypy; _post_sql contract guarantees non-None on err=None

    status = response.status_code
    if status == 200:
        body = response.text.strip()
        if not body:
            return BetterStackValidationResult(
                ok=False,
                detail="Better Stack SQL endpoint returned an empty body for the probe.",
            )
        return BetterStackValidationResult(
            ok=True,
            detail=f"Connected to Better Stack SQL API at {config.query_endpoint}.",
        )
    if status == 401:
        return BetterStackValidationResult(
            ok=False,
            detail="Better Stack authentication failed (check BETTERSTACK_USERNAME / BETTERSTACK_PASSWORD).",
        )
    if status == 404:
        return BetterStackValidationResult(
            ok=False,
            detail=(
                "Better Stack endpoint not found — verify BETTERSTACK_QUERY_ENDPOINT "
                "matches your region (e.g. https://eu-nbg-2-connect.betterstackdata.com)."
            ),
        )
    return BetterStackValidationResult(
        ok=False,
        detail=f"Better Stack API returned HTTP {status}: {response.text[:200]}",
    )


# ---------------------------------------------------------------------------
# Log-query functions
# ---------------------------------------------------------------------------


def _error_evidence(error: str, *, source: str = "") -> dict[str, Any]:
    """Standard error-shape dict returned by query functions on failure."""
    return {
        "source": "betterstack",
        "available": False,
        "error": error,
        "betterstack_source": source,
        "rows": [],
        "row_count": 0,
    }


def _validate_source_name(source: str) -> str | None:
    """Return the source identifier if it is a safe bare identifier, else ``None``."""
    cleaned = (source or "").strip()
    if not cleaned or not _SOURCE_NAME_RE.fullmatch(cleaned):
        return None
    return cleaned


def _validate_iso_timestamp(value: str | None) -> str | None:
    """Pass-through the ISO-8601 timestamp when parseable; else ``None``.

    Used to reject injected SQL fragments before inlining into the WHERE clause.
    """
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return value


def _time_predicate(since: str | None, until: str | None) -> str:
    """Build the shared ``dt >= … AND dt <= …`` WHERE fragment (or empty)."""
    parts: list[str] = []
    if since:
        parts.append(f"dt >= parseDateTime64BestEffort('{since}', 3, 'UTC')")
    if until:
        parts.append(f"dt <= parseDateTime64BestEffort('{until}', 3, 'UTC')")
    return " AND ".join(parts)


def _build_logs_query(
    source: str,
    since: str | None,
    until: str | None,
    limit: int,
) -> str:
    """Build a UNION query over ``remote(<source>_logs)`` and ``s3Cluster(primary, <source>_s3)``.

    Matches the sample query Better Stack's dashboard generates on connection
    creation: recent logs come from the ``remote(...)`` table function; historical
    (archived) log rows live in S3 and are filtered by ``_row_type = 1`` to
    exclude spans and metrics that share the same ``_s3`` collection.
    """
    time_pred = _time_predicate(since, until)
    recent_where = f"\n  WHERE {time_pred}" if time_pred else ""
    hist_conds = ["_row_type = 1"]
    if time_pred:
        hist_conds.append(time_pred)
    hist_where = f"\n  WHERE {' AND '.join(hist_conds)}"
    return (
        "SELECT dt, raw FROM (\n"
        f"  SELECT dt, raw FROM remote({source}_logs){recent_where}\n"
        "  UNION ALL\n"
        f"  SELECT dt, raw FROM s3Cluster(primary, {source}_s3){hist_where}\n"
        ")\n"
        "ORDER BY dt DESC\n"
        f"LIMIT {limit}\n"
        "FORMAT JSONEachRow"
    )


def _parse_json_each_row(body: str) -> list[dict[str, Any]]:
    """Parse a ClickHouse ``FORMAT JSONEachRow`` body into a list of dicts."""
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def query_logs(
    config: BetterStackConfig,
    source: str,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch log rows for a Better Stack source (recent + historical).

    Parameters:
        config: authenticated ``BetterStackConfig``.
        source: Better Stack source identifier (e.g. ``t123456_myapp_logs``);
            must match ``[A-Za-z0-9_]+``. Do NOT include the ``_logs`` or
            ``_s3`` suffix — this function appends them to build the
            ``remote(...)`` and ``s3Cluster(primary, ...)`` table functions.
        since: optional ISO-8601 lower-bound timestamp.
        until: optional ISO-8601 upper-bound timestamp.
        limit: optional row cap; clamped to ``config.max_rows``.
    """
    if not config.is_configured:
        return _error_evidence("Not configured.", source=source)

    safe_source = _validate_source_name(source)
    if safe_source is None:
        return _error_evidence(
            f"Invalid Better Stack source identifier: {source!r}. "
            "Expected a ClickHouse bare identifier (e.g. t123456_myapp).",
            source=source,
        )

    since_sql = _validate_iso_timestamp(since)
    if since and since_sql is None:
        return _error_evidence(
            f"Invalid 'since' timestamp: {since!r}. Expected ISO-8601.",
            source=safe_source,
        )
    until_sql = _validate_iso_timestamp(until)
    if until and until_sql is None:
        return _error_evidence(
            f"Invalid 'until' timestamp: {until!r}. Expected ISO-8601.",
            source=safe_source,
        )

    effective_limit = min(max(1, int(limit or config.max_rows)), config.max_rows)
    sql = _build_logs_query(safe_source, since_sql, until_sql, effective_limit)

    try:
        with _sql_client(config) as client:
            response, err = _post_sql(client, config.query_endpoint, sql)
    except Exception as err:  # noqa: BLE001 — final-resort guard around transport setup
        logger.debug("Better Stack query_logs failed", exc_info=True)
        return _error_evidence(f"Better Stack connection failed: {err}", source=safe_source)

    if err is not None or response is None:
        return _error_evidence(
            err or "Better Stack request returned no response.",
            source=safe_source,
        )

    if response.status_code == 401:
        return _error_evidence(
            "Better Stack authentication failed (check credentials).",
            source=safe_source,
        )
    if response.status_code != 200:
        return _error_evidence(
            f"Better Stack query returned HTTP {response.status_code}: {response.text[:200]}",
            source=safe_source,
        )

    rows = _parse_json_each_row(response.text)
    return {
        "source": "betterstack",
        "available": True,
        "betterstack_source": safe_source,
        "rows": rows,
        "row_count": len(rows),
        "limit": effective_limit,
    }


__all__ = [
    "DEFAULT_BETTERSTACK_MAX_ROWS",
    "DEFAULT_BETTERSTACK_TIMEOUT_S",
    "BetterStackConfig",
    "BetterStackValidationResult",
    "betterstack_config_from_env",
    "betterstack_extract_params",
    "betterstack_is_available",
    "build_betterstack_config",
    "query_logs",
    "validate_betterstack_config",
]
