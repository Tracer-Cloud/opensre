"""Shared Better Stack Telemetry integration helpers.

Better Stack's Telemetry query API is a ClickHouse HTTP interface with Basic
auth. Credentials are generated in the dashboard's ``Integrations → Connect
ClickHouse HTTP client`` panel. SQL is sent as the request body; the endpoint
is region-specific (e.g. ``https://eu-nbg-2-connect.betterstackdata.com``)
and the user supplies it along with the generated username/password.

All operations are production-safe: read-only SQL, timeouts enforced, result
sizes capped. Source tables are named ``t{team_id}_{source}_logs`` and must be
referenced via ClickHouse table functions (``remote(...)`` for recent logs,
``s3Cluster(primary, ...)`` for historical). The SQL API does not expose a
``system.tables`` / ``SHOW TABLES`` endpoint, so source-table discovery is
handled via the optional ``BETTERSTACK_TABLES`` env hint that this module
surfaces to the planner through ``extract_params``.
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
_REQUIRED_CONTENT_TYPE = "plain/text"
_REQUIRED_QUERY_PARAMS = {"output_format_pretty_row_numbers": "0"}
_VALIDATION_PROBE_SQL = "SELECT 1 FORMAT JSONEachRow"
# Better Stack source tables are ``t{team_id}_{source_name}_logs`` — always a
# ClickHouse-safe bare identifier. Anything else in the FROM position would be
# SQL injection, so we reject unless it matches this whitelist.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class BetterStackConfig(StrictConfigModel):
    """Normalized Better Stack SQL Query API connection settings."""

    query_endpoint: str = ""
    username: str = ""
    password: str = ""
    tables: list[str] = Field(default_factory=list)
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

    @field_validator("tables", mode="before")
    @classmethod
    def _normalize_tables(cls, value: Any) -> list[str]:
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
    ``BETTERSTACK_TABLES`` env var is an optional comma-separated hint list
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
            "tables": os.getenv("BETTERSTACK_TABLES", ""),
        }
    )


def betterstack_is_available(sources: dict[str, dict]) -> bool:
    """Check if Better Stack integration credentials are present.

    Only the three auth fields gate availability. ``tables`` is an optional
    planner hint and does not block the tool from being offered.
    """
    bs = sources.get("betterstack", {})
    return bool(bs.get("query_endpoint") and bs.get("username"))


def betterstack_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Better Stack credentials and optional table hints for tool calls."""
    bs = sources.get("betterstack", {})
    return {
        "query_endpoint": bs.get("query_endpoint", ""),
        "username": bs.get("username", ""),
        "password": bs.get("password", ""),
        "tables": list(bs.get("tables", []) or []),
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
            response, err = _post_sql(
                client, config.query_endpoint, _VALIDATION_PROBE_SQL
            )
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


def _error_evidence(error: str, *, table: str = "") -> dict[str, Any]:
    """Standard error-shape dict returned by query functions on failure."""
    return {
        "source": "betterstack",
        "available": False,
        "error": error,
        "table": table,
        "rows": [],
        "row_count": 0,
    }


def _validate_table_name(table: str) -> str | None:
    """Return the table name if it is a safe bare identifier, else ``None``."""
    cleaned = (table or "").strip()
    if not cleaned or not _TABLE_NAME_RE.fullmatch(cleaned):
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


def _build_logs_query(
    table: str,
    since: str | None,
    until: str | None,
    limit: int,
) -> str:
    """Build a ClickHouse ``remote(...)`` query for recent log rows."""
    where_parts: list[str] = []
    if since:
        where_parts.append(
            f"dt >= parseDateTime64BestEffort('{since}', 3, 'UTC')"
        )
    if until:
        where_parts.append(
            f"dt <= parseDateTime64BestEffort('{until}', 3, 'UTC')"
        )
    where_sql = f"\nWHERE {' AND '.join(where_parts)}" if where_parts else ""
    return (
        "SELECT dt, raw\n"
        f"FROM remote({table}){where_sql}\n"
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
    table: str,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fetch recent log rows for a Better Stack source table.

    Parameters:
        config: authenticated ``BetterStackConfig``.
        table: source table name (e.g. ``t123456_myapp_logs``); must match
            ``[A-Za-z0-9_]+``.
        since: optional ISO-8601 lower-bound timestamp.
        until: optional ISO-8601 upper-bound timestamp.
        limit: optional row cap; clamped to ``config.max_rows``.
    """
    if not config.is_configured:
        return _error_evidence("Not configured.", table=table)

    safe_table = _validate_table_name(table)
    if safe_table is None:
        return _error_evidence(
            f"Invalid Better Stack table name: {table!r}. "
            "Expected a ClickHouse bare identifier (e.g. t123456_myapp_logs).",
            table=table,
        )

    since_sql = _validate_iso_timestamp(since)
    if since and since_sql is None:
        return _error_evidence(
            f"Invalid 'since' timestamp: {since!r}. Expected ISO-8601.",
            table=safe_table,
        )
    until_sql = _validate_iso_timestamp(until)
    if until and until_sql is None:
        return _error_evidence(
            f"Invalid 'until' timestamp: {until!r}. Expected ISO-8601.",
            table=safe_table,
        )

    effective_limit = min(int(limit or config.max_rows), config.max_rows)
    sql = _build_logs_query(safe_table, since_sql, until_sql, effective_limit)

    try:
        with _sql_client(config) as client:
            response, err = _post_sql(client, config.query_endpoint, sql)
    except Exception as err:  # noqa: BLE001 — final-resort guard around transport setup
        logger.debug("Better Stack query_logs failed", exc_info=True)
        return _error_evidence(
            f"Better Stack connection failed: {err}", table=safe_table
        )

    if err is not None or response is None:
        return _error_evidence(
            err or "Better Stack request returned no response.",
            table=safe_table,
        )

    if response.status_code == 401:
        return _error_evidence(
            "Better Stack authentication failed (check credentials).",
            table=safe_table,
        )
    if response.status_code != 200:
        return _error_evidence(
            f"Better Stack query returned HTTP {response.status_code}: "
            f"{response.text[:200]}",
            table=safe_table,
        )

    rows = _parse_json_each_row(response.text)
    return {
        "source": "betterstack",
        "available": True,
        "table": safe_table,
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
