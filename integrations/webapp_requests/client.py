"""How a hosted gateway talks to the web app's agent routes.

Every call carries the gateway's own org-scoped token (the routes refuse the
shared fleet secret). The web app never calls into a gateway: the gateway
reports mirrors (``PUT``) and pulls the changes members asked for, then
acknowledges each one (opensre-webapp ``docs/scheduled-loops.md``,
``docs/knowledge.md``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from config.account import resolve_account_token
from config.constants.billing import WEBAPP_URL_ENV
from config.constants.organization import organization_id

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class WebappAgentTarget:
    """Where and as whom this gateway calls the web app."""

    base_url: str
    organization: str
    token: str


def webapp_agent_target() -> WebappAgentTarget | None:
    """The web app this hosted gateway reports to, or ``None`` off a hosted organization.

    Needs the organization id, an https web app URL and the gateway token.
    """
    org = organization_id()
    base_url = (os.getenv(WEBAPP_URL_ENV) or "").strip()
    token = resolve_account_token()
    if not org or not token or not base_url.lower().startswith("https://"):
        return None
    return WebappAgentTarget(base_url=base_url.rstrip("/"), organization=org, token=token)


class WebappAgentClient:
    """Calls to one organization's agent routes; never raises to the caller."""

    def __init__(
        self,
        target: WebappAgentTarget,
        *,
        log_tag: str,
        http: Any = httpx,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._target = target
        self._log_tag = log_tag
        self._http = http
        self._timeout = timeout_seconds

    @property
    def organization(self) -> str:
        return self._target.organization

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        """The response, or ``None`` when the web app could not be reached."""
        try:
            response = self._http.request(
                method,
                f"{self._target.base_url}{path}",
                headers={"Authorization": f"Bearer {self._target.token}"},
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "[%s] %s %s failed (%s)", self._log_tag, method, path, type(exc).__name__
            )
            return None
        if response.status_code not in (HTTPStatus.OK, HTTPStatus.NOT_FOUND):
            # The web app's own short error text, so a rejected report says why.
            logger.warning(
                "[%s] %s %s -> %s %s",
                self._log_tag,
                method,
                path,
                response.status_code,
                _error_text(response),
            )
        return response

    def pending_requests(self, path: str) -> list[Mapping[str, Any]] | None:
        """The pending requests at ``path``, oldest first; ``None`` when they could not be read."""
        response = self.call("GET", path, params={"organizationId": self.organization})
        if response is None or response.status_code != HTTPStatus.OK:
            return None
        try:
            data = response.json().get("data")
        except (ValueError, AttributeError):
            return None
        if not isinstance(data, list):
            return None
        return [item for item in data if isinstance(item, Mapping) and item.get("id")]

    def acknowledge(self, path: str, request_id: str, body: Mapping[str, Any]) -> None:
        """Report one request's outcome; a 404 (already settled) is fine."""
        self.call(
            "POST", f"{path}/{request_id}", json={"organizationId": self.organization, **body}
        )


def _error_text(response: Any) -> str:
    """The ``error`` field of a web app error response, capped; "" when absent."""
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return ""
    return str(error)[:200] if error else ""


__all__ = ["WebappAgentClient", "WebappAgentTarget", "webapp_agent_target"]
