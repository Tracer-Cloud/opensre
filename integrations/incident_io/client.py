"""incident.io REST API client for investigation context and write-back."""

from __future__ import annotations

import email.utils
import logging
import random
import re
import threading
import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

import httpx

from integrations.config_models import IncidentIoIntegrationConfig
from integrations.probes import ProbeResult

logger = logging.getLogger(__name__)

IncidentIoConfig = IncidentIoIntegrationConfig

_DEFAULT_TIMEOUT = 30
_MAX_RETRIES = 3
_APPEND_SUMMARY_VERIFY_ATTEMPTS = 5
_RETRYABLE_STATUS_CODES = {
    HTTPStatus.TOO_MANY_REQUESTS,
    HTTPStatus.INTERNAL_SERVER_ERROR,
    HTTPStatus.BAD_GATEWAY,
    HTTPStatus.SERVICE_UNAVAILABLE,
    HTTPStatus.GATEWAY_TIMEOUT,
}
_IDEMPOTENT_METHODS = {"GET", "HEAD", "OPTIONS"}
_SECRET_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*[:=]\s*\S+"
    r"|incident[_-]?io[_-]?(api[_-]?)?key\s*[:=]\s*\S+"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)

# Module-level write locks keyed by incident ID so concurrent *thread* invocations
# within the same process that target the same incident are serialised.  Each
# distinct active incident adds one Lock (~50 bytes); in practice the set is
# bounded by the number of incidents in flight during an investigation session.
_INCIDENT_WRITE_LOCKS: dict[str, threading.Lock] = {}
_INCIDENT_WRITE_LOCKS_META = threading.Lock()


def _get_incident_write_lock(incident_id: str) -> threading.Lock:
    with _INCIDENT_WRITE_LOCKS_META:
        if incident_id not in _INCIDENT_WRITE_LOCKS:
            _INCIDENT_WRITE_LOCKS[incident_id] = threading.Lock()
        return _INCIDENT_WRITE_LOCKS[incident_id]


def _safe_int(value: int | None, default: int, maximum: int) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def _retry_after_from_response(response: httpx.Response) -> float | None:
    header_retry_after = _parse_retry_after(response.headers.get("Retry-After"))
    if header_retry_after is not None:
        return header_retry_after
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return None
    return _parse_retry_after(str(rate_limit.get("retry_after") or ""))


class IncidentIoClient:
    """Synchronous client for the incident.io v2 API."""

    def __init__(self, config: IncidentIoConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None
        self._client_lock = threading.RLock()

    @property
    def is_configured(self) -> bool:
        return bool(self.config.api_key)

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.config.base_url,
                    headers=self.config.headers,
                    timeout=_DEFAULT_TIMEOUT,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def __enter__(self) -> IncidentIoClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _redact(self, value: object) -> str:
        text = str(value)
        if self.config.api_key:
            text = text.replace(self.config.api_key, "[REDACTED]")
        return _SECRET_RE.sub("[REDACTED]", text)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        method_upper = method.upper()

        for attempt in range(_MAX_RETRIES + 1):
            try:
                response = self._get_client().request(method_upper, path, **kwargs)
                if response.status_code not in _RETRYABLE_STATUS_CODES:
                    return response
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                should_retry = (
                    status_code == HTTPStatus.TOO_MANY_REQUESTS
                    or method_upper in _IDEMPOTENT_METHODS
                )
                if status_code not in _RETRYABLE_STATUS_CODES or not should_retry:
                    raise
                if attempt >= _MAX_RETRIES:
                    raise
                retry_after = _retry_after_from_response(exc.response)
                sleep_for = retry_after if retry_after is not None else (2**attempt)
                sleep_for += random.random() * 0.1
                logger.warning(
                    "[incident_io] %s %s returned HTTP %s; retrying in %.2fs",
                    method_upper,
                    path,
                    status_code,
                    sleep_for,
                )
                time.sleep(sleep_for)
            except httpx.RequestError as exc:
                if method_upper not in _IDEMPOTENT_METHODS or attempt >= _MAX_RETRIES:
                    raise
                sleep_for = (2**attempt) + (random.random() * 0.1)
                logger.warning(
                    "[incident_io] %s %s request failed; retrying in %.2fs: %s",
                    method_upper,
                    path,
                    sleep_for,
                    self._redact(exc),
                )
                time.sleep(sleep_for)

        raise AssertionError("incident.io _request loop exited without return or raise")

    def probe_access(self) -> ProbeResult:
        """Validate credentials with a minimal incident list request."""
        if not self.is_configured:
            return ProbeResult.missing("Missing API key.")
        try:
            response = self._request("GET", "/v2/incidents", params={"page_size": 1})
            response.raise_for_status()
        except Exception as exc:
            return ProbeResult.failed(
                f"Connection failed: {self._redact(exc)}",
                base_url=self.config.base_url,
            )
        return ProbeResult.passed(
            "Connected to incident.io; API key accepted.",
            base_url=self.config.base_url,
        )

    def list_incidents(
        self,
        *,
        status_category: str = "live",
        page_size: int | None = 20,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List incidents, filtered by incident.io status category when supplied."""
        params: dict[str, Any] = {"page_size": _safe_int(page_size, 20, 500)}
        if status_category:
            params["status_category[one_of]"] = status_category
        if after:
            params["after"] = after

        try:
            response = self._request("GET", "/v2/incidents", params=params)
            response.raise_for_status()
            data = response.json()
            incidents = [_format_incident(item) for item in data.get("incidents", [])]
            result: dict[str, Any] = {
                "success": True,
                "incidents": incidents,
                "total": len(incidents),
            }
            if "pagination_meta" in data:
                result["pagination_meta"] = data["pagination_meta"]
            return result
        except httpx.HTTPStatusError as exc:
            err_text = self._redact(exc.response.text[:300])
            logger.warning(
                "[incident_io] List incidents HTTP failure status=%s error=%r",
                exc.response.status_code,
                err_text,
            )
            return {"success": False, "error": f"HTTP {exc.response.status_code}: {err_text}"}
        except Exception as exc:
            err_text = self._redact(exc)
            logger.warning("[incident_io] List incidents error: %s", err_text)
            return {"success": False, "error": err_text}

    def get_incident(self, incident_id: str) -> dict[str, Any]:
        """Fetch full details for a specific incident."""
        try:
            response = self._request("GET", f"/v2/incidents/{incident_id}")
            response.raise_for_status()
            incident = _format_incident(response.json().get("incident", {}), full=True)
            return {"success": True, "incident": incident}
        except httpx.HTTPStatusError as exc:
            err_text = self._redact(exc.response.text[:300])
            logger.warning(
                "[incident_io] Get incident HTTP failure status=%s id=%r error=%r",
                exc.response.status_code,
                incident_id,
                err_text,
            )
            return {"success": False, "error": f"HTTP {exc.response.status_code}: {err_text}"}
        except Exception as exc:
            err_text = self._redact(exc)
            logger.warning("[incident_io] Get incident error: %s", err_text)
            return {"success": False, "error": err_text}

    def list_incident_updates(
        self,
        incident_id: str,
        *,
        page_size: int | None = 25,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List incident updates, which provide incident timeline/status context."""
        params: dict[str, Any] = {
            "incident_id": incident_id,
            "page_size": _safe_int(page_size, 25, 250),
        }
        if after:
            params["after"] = after

        try:
            response = self._request("GET", "/v2/incident_updates", params=params)
            response.raise_for_status()
            data = response.json()
            updates = [_format_update(item) for item in data.get("incident_updates", [])]
            result: dict[str, Any] = {
                "success": True,
                "incident_updates": updates,
                "total": len(updates),
            }
            if "pagination_meta" in data:
                result["pagination_meta"] = data["pagination_meta"]
            return result
        except httpx.HTTPStatusError as exc:
            err_text = self._redact(exc.response.text[:300])
            logger.warning(
                "[incident_io] List updates HTTP failure status=%s id=%r error=%r",
                exc.response.status_code,
                incident_id,
                err_text,
            )
            return {"success": False, "error": f"HTTP {exc.response.status_code}: {err_text}"}
        except Exception as exc:
            err_text = self._redact(exc)
            logger.warning("[incident_io] List updates error: %s", err_text)
            return {"success": False, "error": err_text}

    def get_incident_context(
        self,
        incident_id: str,
        *,
        update_limit: int | None = 25,
    ) -> dict[str, Any]:
        """Fetch incident metadata and update timeline context in one call."""
        incident_result = self.get_incident(incident_id)
        if not incident_result.get("success"):
            return incident_result
        updates_result = self.list_incident_updates(incident_id, page_size=update_limit)
        if not updates_result.get("success"):
            return {
                "success": False,
                "incident": incident_result.get("incident", {}),
                "error": updates_result.get("error", "unknown error"),
            }
        context_result: dict[str, Any] = {
            "success": True,
            "incident": incident_result.get("incident", {}),
            "incident_updates": updates_result.get("incident_updates", []),
            "total_updates": updates_result.get("total", 0),
        }
        if "pagination_meta" in updates_result:
            context_result["pagination_meta"] = updates_result["pagination_meta"]
        return context_result

    def append_summary_update(
        self,
        incident_id: str,
        *,
        title: str,
        body: str = "",
        notify_incident_channel: bool = False,
    ) -> dict[str, Any]:
        """Append OpenSRE findings to an incident summary via the supported edit endpoint.

        Uses read-post-verify with retries so concurrent writers (including separate OS
        processes) typically converge: if another writer posts between our read and
        write, verification misses our appended text and we merge again from the latest
        summary before re-posting.
        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        finding = f"\n\n---\n**OpenSRE finding: {title}** ({timestamp})"
        if body:
            finding = f"{finding}\n{body}"

        last_error: str | None = None
        for verify_attempt in range(_APPEND_SUMMARY_VERIFY_ATTEMPTS):
            with _get_incident_write_lock(incident_id):
                incident_result = self.get_incident(incident_id)
                if not incident_result.get("success"):
                    return incident_result

                incident = incident_result.get("incident", {})
                current_summary = str(incident.get("summary") or "")
                if finding in current_summary:
                    return {"success": True, "summary": current_summary.strip()}

                updated_summary = (current_summary + finding).strip()
                payload = {
                    "incident": {"summary": updated_summary},
                    "notify_incident_channel": notify_incident_channel,
                }
                try:
                    response = self._request(
                        "POST",
                        f"/v2/incidents/{incident_id}/actions/edit",
                        json=payload,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    err_text = self._redact(exc.response.text[:300])
                    logger.warning(
                        "[incident_io] Summary append HTTP failure status=%s id=%r error=%r",
                        exc.response.status_code,
                        incident_id,
                        err_text,
                    )
                    return {
                        "success": False,
                        "error": f"HTTP {exc.response.status_code}: {err_text}",
                    }
                except Exception as exc:
                    err_text = self._redact(exc)
                    logger.warning("[incident_io] Summary append error: %s", err_text)
                    return {"success": False, "error": err_text}

                verify_result = self.get_incident(incident_id)
                if not verify_result.get("success"):
                    last_error = str(verify_result.get("error", "verify fetch failed"))
                    logger.warning(
                        "[incident_io] Summary verify GET failed id=%r attempt=%s: %s",
                        incident_id,
                        verify_attempt + 1,
                        last_error,
                    )
                else:
                    verified = str(verify_result.get("incident", {}).get("summary") or "")
                    if finding in verified:
                        return {"success": True, "summary": verified.strip()}
                    last_error = "summary verify mismatch after edit (concurrent writer?)"

            if verify_attempt >= _APPEND_SUMMARY_VERIFY_ATTEMPTS - 1:
                break
            # Retry outside the per-incident lock so another process can finish its write.
            sleep_for = (0.2 * (2**verify_attempt)) + random.random() * 0.05
            logger.warning(
                "[incident_io] Summary append verify retry id=%r attempt=%s in %.2fs: %s",
                incident_id,
                verify_attempt + 1,
                sleep_for,
                last_error,
            )
            time.sleep(sleep_for)

        return {
            "success": False,
            "error": last_error or "summary append failed verification after retries",
        }


def _format_incident(data: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    status = data.get("incident_status") or {}
    severity = data.get("severity") or {}
    incident_type = data.get("incident_type") or {}
    formatted: dict[str, Any] = {
        "id": data.get("id", ""),
        "reference": data.get("reference", ""),
        "name": data.get("name", ""),
        "permalink": data.get("permalink", ""),
        "status": status.get("name", ""),
        "status_category": status.get("category", ""),
        "severity": severity.get("name", ""),
        "severity_rank": severity.get("rank"),
        "incident_type": incident_type.get("name", ""),
        "summary": data.get("summary", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
    }
    if full:
        formatted.update(
            {
                "custom_field_entries": data.get("custom_field_entries", []),
                "incident_role_assignments": data.get("incident_role_assignments", []),
                "incident_timestamp_values": data.get("incident_timestamp_values", []),
                "duration_metrics": data.get("duration_metrics", []),
                "external_issue_reference": data.get("external_issue_reference"),
                "slack_channel_name": data.get("slack_channel_name", ""),
                "visibility": data.get("visibility", ""),
            }
        )
    return formatted


def _format_update(data: dict[str, Any]) -> dict[str, Any]:
    status = data.get("new_incident_status") or {}
    severity = data.get("new_severity") or {}
    updater = data.get("updater") or {}
    user = updater.get("user") or {}
    api_key = updater.get("api_key") or {}
    return {
        "id": data.get("id", ""),
        "incident_id": data.get("incident_id", ""),
        "created_at": data.get("created_at", ""),
        "message": data.get("message", ""),
        "new_status": status.get("name", ""),
        "new_status_category": status.get("category", ""),
        "new_severity": severity.get("name", ""),
        "updater": {
            "user_name": user.get("name", ""),
            "user_email": user.get("email", ""),
            "api_key_name": api_key.get("name", ""),
        },
    }


def make_incident_io_client(
    api_key: str | None,
    region: str | None = None,
    *,
    base_url: str | None = "",
) -> IncidentIoClient | None:
    """Create an incident.io client if a usable API key is provided."""
    token = (api_key or "").strip()
    if not token:
        return None
    try:
        # ``region`` is accepted for backward compatibility with early integration
        # drafts, but incident.io documents a single public API host.
        _ = region
        config = IncidentIoConfig(api_key=token, base_url=base_url or "")
        return IncidentIoClient(config)
    except Exception as exc:
        logger.warning("[incident_io] Failed to build client config: %s", exc)
        return None
