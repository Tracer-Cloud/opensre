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

import os
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_BETTERSTACK_TIMEOUT_S = 15
DEFAULT_BETTERSTACK_MAX_ROWS = 500
_MAX_ALLOWED_ROWS = 10_000


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


__all__ = [
    "DEFAULT_BETTERSTACK_MAX_ROWS",
    "DEFAULT_BETTERSTACK_TIMEOUT_S",
    "BetterStackConfig",
    "betterstack_config_from_env",
    "betterstack_extract_params",
    "betterstack_is_available",
    "build_betterstack_config",
]
