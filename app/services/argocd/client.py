"""Argo CD REST API client.

Wraps the read-only Argo CD API endpoints used by investigation tools and
integration verification. Credentials come from the local integration store or
from environment variables resolved by ``app.integrations.catalog``.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import field_validator, model_validator

from app.strict_config import StrictConfigModel
from app.utils.url_validation import validate_https_or_loopback_http_url

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0
_MAX_DIFF_CHARS = 10_000
_SECRET_LINE_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|bearer)\s*[:=]"
)

_GENERIC_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._~+/=-]{6,}"
    r"|authorization\s*[:=]\s*\S+"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)
_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|auth|private[_-]?key|"
    r"client[_-]?secret|connection[_-]?string|credential)"
)


def _normalize_verify_ssl(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off"}:
        return False
    if text in {"1", "true", "yes", "on"}:
        return True
    return bool(value)


class ArgoCDConfig(StrictConfigModel):
    """Normalized Argo CD connection settings."""

    base_url: str
    bearer_token: str = ""
    username: str = ""
    password: str = ""
    project: str = ""
    app_namespace: str = ""
    verify_ssl: bool = True
    integration_id: str = ""

    @field_validator("base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: object) -> str:
        normalized = str(value or "").strip().rstrip("/")
        return validate_https_or_loopback_http_url(normalized, service_name="Argo CD")

    @field_validator("bearer_token", mode="before")
    @classmethod
    def _normalize_bearer_token(cls, value: object) -> str:
        text = str(value or "").strip()
        if text.lower().startswith("bearer "):
            text = text.split(None, 1)[1].strip()
        return text

    @field_validator(
        "username", "password", "project", "app_namespace", "integration_id", mode="before"
    )
    @classmethod
    def _normalize_str(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("verify_ssl", mode="before")
    @classmethod
    def _normalize_bool(cls, value: object) -> bool:
        return _normalize_verify_ssl(value)

    @model_validator(mode="after")
    def _no_dual_auth(self) -> ArgoCDConfig:
        if self.bearer_token and (self.username or self.password):
            raise ValueError(
                "Argo CD config has both bearer_token and username/password set; "
                "use one auth method only."
            )
        return self

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and (self.bearer_token or (self.username and self.password)))


class ArgoCDClient:
    """Synchronous read-only client for Argo CD's REST API."""

    def __init__(
        self,
        config: ArgoCDConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._client: httpx.Client | None = None
        self._session_token = ""
        self._retired_session_tokens: set[str] = set()

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=_DEFAULT_TIMEOUT,
                verify=self.config.verify_ssl,
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> ArgoCDClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def _redact(self, value: object) -> str:
        text = str(value)
        for secret in (
            self.config.bearer_token,
            self.config.password,
            self._session_token,
            *self._retired_session_tokens,
        ):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        text = _GENERIC_SECRET_VALUE_RE.sub("[REDACTED]", text)
        return re.sub(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s+([A-Za-z0-9._~+/=-]{6,})",
            r"\1 [REDACTED]",
            text,
        )

    def _error_result(self, prefix: str, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, httpx.HTTPStatusError):
            response_text = self._redact(exc.response.text[:2000])
            return {
                "success": False,
                "error": f"HTTP {exc.response.status_code}: {response_text}",
            }
        return {"success": False, "error": self._redact(f"{prefix}: {exc}")}

    def _ensure_session_token(self) -> str:
        if self.config.bearer_token:
            return self.config.bearer_token
        if self._session_token:
            return self._session_token
        if not (self.config.username and self.config.password):
            return ""

        # Investigation tools issue short-lived read-only calls. If Argo CD expires
        # a session token mid-run, _request clears the cached token, retries once,
        # and redacts both active and retired tokens in any surfaced error.
        response = self._get_client().post(
            "/api/v1/session",
            json={"username": self.config.username, "password": self.config.password},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("token", "")).strip() if isinstance(payload, dict) else ""
        self._session_token = token
        return token

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(2):
            request_kwargs = dict(kwargs)
            token = self._ensure_session_token()
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            headers.update(request_kwargs.pop("headers", {}) or {})
            response = self._get_client().request(method, path, headers=headers, **request_kwargs)
            try:
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code == 401
                    and self._session_token
                    and not self.config.bearer_token
                ):
                    self._retired_session_tokens.add(self._session_token)
                    self._session_token = ""
                    if attempt == 0:
                        continue
                raise
        raise RuntimeError("Argo CD request retry exhausted")

    def list_applications(
        self,
        *,
        projects: list[str] | None = None,
        selector: str = "",
    ) -> dict[str, Any]:
        """List Argo CD applications, optionally filtered by projects or selector."""
        params: dict[str, Any] = {}
        cleaned_projects = [
            str(project).strip() for project in (projects or []) if str(project).strip()
        ]
        if cleaned_projects:
            params["projects"] = ",".join(cleaned_projects)
        cleaned_selector = str(selector or "").strip()
        if cleaned_selector:
            params["selector"] = cleaned_selector
        try:
            response = self._request("GET", "/api/v1/applications", params=params)
            payload = response.json()
            items = payload.get("items", []) if isinstance(payload, dict) else []
            applications = [
                _normalize_application(item) for item in items if isinstance(item, dict)
            ]
            return {"success": True, "applications": applications, "total": len(applications)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[argocd] list_applications failed type=%s", type(exc).__name__)
            return self._error_result("list applications failed", exc)

    def get_application_summary(
        self,
        application_name: str,
        *,
        project: str = "",
        app_namespace: str = "",
    ) -> dict[str, Any]:
        """Fetch one Argo CD application and return a compact status summary."""
        name = str(application_name or "").strip()
        if not name:
            return {"success": False, "error": "application_name is required"}
        params = _application_params(
            project or self.config.project, app_namespace or self.config.app_namespace
        )
        try:
            response = self._request(
                "GET",
                f"/api/v1/applications/{quote(name, safe='')}",
                params=params,
            )
            payload = response.json()
            if not isinstance(payload, dict):
                return {"success": False, "error": "unexpected application response"}
            app = _normalize_application(payload)
            return {
                "success": True,
                "application": app,
                "recent_history": _recent_history(payload),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[argocd] get_application_summary failed type=%s app=%r",
                type(exc).__name__,
                name,
            )
            return self._error_result("get application summary failed", exc)

    def get_application_diff(
        self,
        application_name: str,
        *,
        project: str = "",
        app_namespace: str = "",
    ) -> dict[str, Any]:
        """Fetch Argo CD diff data for one application."""
        name = str(application_name or "").strip()
        if not name:
            return {"success": False, "error": "application_name is required"}
        params = _application_params(
            project or self.config.project, app_namespace or self.config.app_namespace
        )
        try:
            response = self._request(
                "GET",
                f"/api/v1/applications/{quote(name, safe='')}/server-side-diff",
                params=params,
            )
            payload = response.json()
            raw_diffs: list[Any] = []
            payload_modified = False
            if isinstance(payload, dict):
                # Argo CD v3.3 exposes this response as {items, modified}; keep
                # accepting the older/tested {diffs} shape for compatibility.
                raw_diffs = payload.get("diffs") or payload.get("items") or []
                payload_modified = bool(payload.get("modified"))
            diffs = [
                _normalize_diff(diff)
                for diff in raw_diffs
                if isinstance(diff, dict) and _resource_diff_is_modified(diff)
            ]
            if not diffs:
                diffs = self._get_managed_resource_diffs(name, params=params)
            return {
                "success": True,
                "application_name": name,
                "drift_detected": bool(diffs) or payload_modified,
                "diffs": diffs,
                "diff_count": len(diffs),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[argocd] get_application_diff failed type=%s app=%r",
                type(exc).__name__,
                name,
            )
            return self._error_result("get application diff failed", exc)

    def _get_managed_resource_diffs(
        self,
        application_name: str,
        *,
        params: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Derive drift from Argo CD managed resource target/live states."""
        response = self._request(
            "GET",
            f"/api/v1/applications/{quote(application_name, safe='')}/managed-resources",
            params=params,
        )
        payload = response.json()
        raw_items = payload.get("items", []) if isinstance(payload, dict) else []
        diffs: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            diff = _managed_resource_diff(item)
            if diff:
                diffs.append(_normalize_diff(diff))
        return diffs


def _application_params(project: str = "", app_namespace: str = "") -> dict[str, str]:
    params: dict[str, str] = {}
    if project:
        params["project"] = project
    if app_namespace:
        params["appNamespace"] = app_namespace
    return params


def _normalize_application(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
    spec = payload.get("spec", {}) if isinstance(payload.get("spec"), dict) else {}
    source = spec.get("source", {}) if isinstance(spec.get("source"), dict) else {}
    destination = spec.get("destination", {}) if isinstance(spec.get("destination"), dict) else {}
    status = payload.get("status", {}) if isinstance(payload.get("status"), dict) else {}
    sync = status.get("sync", {}) if isinstance(status.get("sync"), dict) else {}
    health = status.get("health", {}) if isinstance(status.get("health"), dict) else {}
    operation_state = (
        status.get("operationState", {}) if isinstance(status.get("operationState"), dict) else {}
    )
    sync_result = (
        operation_state.get("syncResult", {})
        if isinstance(operation_state.get("syncResult"), dict)
        else {}
    )
    history = status.get("history", []) if isinstance(status.get("history"), list) else []
    summary = status.get("summary", {}) if isinstance(status.get("summary"), dict) else {}
    revision = (
        str(sync.get("revision") or "").strip()
        or str(sync_result.get("revision") or "").strip()
        or _history_revision(history)
    )
    return {
        "name": str(metadata.get("name", "")).strip(),
        "namespace": str(metadata.get("namespace", "")).strip(),
        "project": str(spec.get("project", "")).strip(),
        "repo_url": str(source.get("repoURL", "")).strip(),
        "target_revision": str(source.get("targetRevision", "")).strip(),
        "destination_server": str(destination.get("server", "")).strip(),
        "destination_namespace": str(destination.get("namespace", "")).strip(),
        "sync_status": str(sync.get("status", "")).strip(),
        "health_status": str(health.get("status", "")).strip(),
        "health_message": str(health.get("message", "")).strip(),
        "revision": revision,
        "operation_phase": str(operation_state.get("phase", "")).strip(),
        "operation_message": str(operation_state.get("message", "")).strip(),
        "images": list(summary.get("images", []) or [])
        if isinstance(summary.get("images", []), list)
        else [],
        "history_count": len(history),
    }


def _history_revision(history: list[Any]) -> str:
    for item in reversed(history):
        if isinstance(item, dict) and item.get("revision"):
            return str(item["revision"]).strip()
    return ""


def _recent_history(payload: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    status = payload.get("status", {}) if isinstance(payload.get("status"), dict) else {}
    history = status.get("history", []) if isinstance(status.get("history"), list) else []
    recent = [item for item in reversed(history) if isinstance(item, dict)]
    return recent[:limit]


def _resource_diff_is_modified(payload: dict[str, Any]) -> bool:
    if "modified" in payload:
        return bool(payload.get("modified"))
    return bool(
        payload.get("diff")
        or payload.get("liveState")
        or payload.get("normalizedLiveState")
        or payload.get("targetState")
        or payload.get("predictedLiveState")
    )


def _managed_resource_diff(payload: dict[str, Any]) -> dict[str, Any] | None:
    desired = str(payload.get("predictedLiveState") or payload.get("targetState") or "")
    live = str(payload.get("normalizedLiveState") or payload.get("liveState") or "")
    desired_state = _parse_json_state(desired)
    live_state = _parse_json_state(live)
    if desired_state is None or live_state is None:
        # Argo CD managed resource states are expected to be JSON. Avoid
        # synthesizing diffs from plain text/YAML where formatting alone can
        # look like drift.
        return None
    desired_lines = _json_state_lines(desired_state)
    live_lines = _json_state_lines(live_state)
    if desired_lines == live_lines:
        return None
    if _json_contains_sensitive_data(desired_state) or _json_contains_sensitive_data(live_state):
        rendered_diff = "[REDACTED secret-bearing resource diff]"
    else:
        rendered_diff = _render_state_diff(desired_lines, live_lines)
    return {
        "group": payload.get("group", ""),
        "kind": payload.get("kind", ""),
        "name": payload.get("name", ""),
        "namespace": payload.get("namespace", ""),
        "diff": rendered_diff,
    }


def _parse_json_state(value: str) -> Any | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _json_state_lines(value: Any) -> list[str]:
    return json.dumps(value, indent=2, sort_keys=True).splitlines()


def _json_contains_sensitive_data(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _SENSITIVE_FIELD_RE.search(str(key)):
                return True
            if _json_contains_sensitive_data(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_json_contains_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return bool(_GENERIC_SECRET_VALUE_RE.search(value))
    return False


def _render_state_diff(desired_lines: list[str], live_lines: list[str]) -> str:
    return "\n".join(
        difflib.unified_diff(
            desired_lines,
            live_lines,
            fromfile="desired",
            tofile="live",
            lineterm="",
        )
    )


def _sanitize_diff(value: object, *, resource_kind: str = "") -> str:
    if str(resource_kind or "").strip().lower() == "secret":
        return "[REDACTED Kubernetes Secret diff]"

    lines: list[str] = []
    for line in str(value or "").splitlines():
        if _SECRET_LINE_RE.search(line) or _GENERIC_SECRET_VALUE_RE.search(line):
            lines.append("[REDACTED secret-bearing diff line]")
        else:
            lines.append(line)
    text = "\n".join(lines)
    if len(text) > _MAX_DIFF_CHARS:
        return f"{text[:_MAX_DIFF_CHARS]}\n[truncated after {_MAX_DIFF_CHARS} chars]"
    return text


def _normalize_diff(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "group": str(payload.get("group", "")).strip(),
        "kind": str(payload.get("kind", "")).strip(),
        "name": str(payload.get("name", "")).strip(),
        "namespace": str(payload.get("namespace", "")).strip(),
        "diff": _sanitize_diff(payload.get("diff", ""), resource_kind=payload.get("kind", "")),
    }


def make_argocd_client(
    base_url: str | None,
    bearer_token: str | None = None,
    username: str | None = None,
    password: str | None = None,
    *,
    project: str | None = None,
    app_namespace: str | None = None,
    verify_ssl: bool | str = True,
) -> ArgoCDClient | None:
    """Create an ArgoCDClient when a URL and auth method are available."""
    url = (base_url or "").strip().rstrip("/")
    token = (bearer_token or "").strip()
    user = (username or "").strip()
    pw = (password or "").strip()
    if not url or not (token or (user and pw)):
        return None
    try:
        return ArgoCDClient(
            ArgoCDConfig(
                base_url=url,
                bearer_token=token,
                username=user,
                password=pw,
                project=project or "",
                app_namespace=app_namespace or "",
                verify_ssl=_normalize_verify_ssl(verify_ssl),
            )
        )
    except Exception:
        return None
