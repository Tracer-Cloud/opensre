"""Base HTTP client for Grafana Cloud API."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import requests

from integrations.grafana.config import GrafanaAccountConfig

logger = logging.getLogger(__name__)

# Client-side transport failures (host unreachable). Soft empty results after
# these mislead gather: tools look successful and the circuit breaker never
# marks Grafana, so SessionGoal turns re-pay connect timeouts.
_TRANSPORT_ERROR_MARKERS = (
    "connect timeout",
    "connecttimeouterror",
    "connection refused",
    "connection timed out",
    "max retries exceeded",
    "name or service not known",
    "temporary failure in name resolution",
    "no route to host",
    "network is unreachable",
    "httpconnectionpool",
)


def _is_transport_failure(exc: BaseException) -> bool:
    """True when ``exc`` is a requests/urllib3 failure to reach Grafana itself."""
    if isinstance(
        exc,
        (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ),
    ):
        return True
    lowered = str(exc).lower()
    return any(marker in lowered for marker in _TRANSPORT_ERROR_MARKERS)


def _extract_datasource_uid(rule: dict) -> str:
    """Extract the primary datasource UID from an alert rule."""
    alert = rule.get("grafana_alert", {})
    for datum in alert.get("data", []):
        model = datum.get("model", {})
        ds = model.get("datasource", {})
        uid = ds.get("uid")
        if isinstance(uid, str) and uid:
            return uid
    return ""


def _extract_rule_queries(rule: dict) -> list[dict]:
    """Extract query expressions from an alert rule."""
    alert = rule.get("grafana_alert", {})
    queries = []
    for datum in alert.get("data", []):
        model = datum.get("model", {})
        expr = model.get("expr", "")
        if expr:
            queries.append(
                {
                    "ref_id": datum.get("refId", ""),
                    "expr": expr,
                    "datasource_uid": model.get("datasource", {}).get("uid", ""),
                }
            )
    return queries


def _epoch_ms_to_iso(ms: Any) -> str | None:
    """Convert a Grafana epoch-millisecond timestamp to an ISO 8601 UTC string."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError):
        return None


def _map_annotation(item: dict[str, Any]) -> dict[str, Any]:
    """Map a raw /api/annotations item to the tool-facing annotation shape."""
    tags = item.get("tags")
    return {
        "time": _epoch_ms_to_iso(item.get("time")),
        "time_end": _epoch_ms_to_iso(item.get("timeEnd")),
        "text": item.get("text", ""),
        "tags": tags if isinstance(tags, list) else [],
        # Modern UID only; ignore the legacy numeric dashboardId (0 == not attached).
        "dashboard_uid": item.get("dashboardUID") or None,
    }


class GrafanaClientBase:
    """Base HTTP client with common request methods for Grafana Cloud."""

    def __init__(self, config: GrafanaAccountConfig):
        self._config = config
        self.account_id = config.account_id
        self.instance_url = config.instance_url
        self.read_token = config.read_token
        self.username = config.username
        self.password = config.password
        self.loki_datasource_uid = config.loki_datasource_uid
        self.tempo_datasource_uid = config.tempo_datasource_uid
        self.mimir_datasource_uid = config.mimir_datasource_uid
        self.uses_local_anonymous_auth = config.uses_local_anonymous_auth

    @property
    def is_configured(self) -> bool:
        return self._config.is_configured

    @property
    def ssl_verify(self) -> bool | str:
        """Value to pass as ``requests``' ``verify=`` kwarg for this account."""
        return self._config.ssl_verify

    def _build_datasource_url(self, datasource_uid: str, path: str) -> str:
        return f"{self.instance_url}/api/datasources/proxy/uid/{datasource_uid}{path}"

    def build_logql_query(
        self,
        service_name: str,
        *,
        correlation_id: str | None = None,
        execution_run_id: str | None = None,
    ) -> str:
        base = f'{{service_name="{service_name}"}}'
        filters: list[str] = []

        if execution_run_id:
            filters.append(execution_run_id)
        if correlation_id and correlation_id != execution_run_id:
            filters.append(correlation_id)

        for value in filters:
            base += f' |= "{value}"'

        return base

    def build_explore_url(
        self,
        *,
        query: str,
        datasource_uid: str,
        from_time: str = "now-1h",
        to_time: str = "now",
    ) -> str:
        left = [from_time, to_time, datasource_uid, {"expr": query, "refId": "A"}]
        left_param = quote(json.dumps(left, separators=(",", ":")))
        return f"{self.instance_url.rstrip('/')}/explore?orgId=1&left={left_param}"

    def build_loki_explore_url(
        self,
        service_name: str,
        *,
        correlation_id: str | None = None,
        execution_run_id: str | None = None,
        from_time: str = "now-1h",
        to_time: str = "now",
    ) -> str:
        if not self.instance_url:
            return ""

        query = self.build_logql_query(
            service_name,
            correlation_id=correlation_id,
            execution_run_id=execution_run_id,
        )
        return self.build_explore_url(
            query=query,
            datasource_uid=self.loki_datasource_uid,
            from_time=from_time,
            to_time=to_time,
        )

    # Datasource type keywords used to classify each datasource
    _TYPE_MAP = {
        "loki": "loki_uid",
        "tempo": "tempo_uid",
        "prometheus": "mimir_uid",
    }

    # UIDs/names containing these substrings are internal/secondary datasources
    # that should be deprioritized in favour of the primary ones.
    _DEPRIORITIZE_KEYWORDS = ("alert", "state-history", "ml-", "usage-insights")

    # UIDs/names containing these substrings are strong signals for primary datasources.
    _PRIMARY_HINTS: dict[str, list[str]] = {
        "loki_uid": ["logs", "-log"],
        "tempo_uid": ["traces", "-trace"],
        "mimir_uid": ["prom", "metrics", "-metric"],
    }

    def discover_datasource_uids(self) -> dict[str, str]:
        """Discover datasource UIDs by querying GET /api/datasources.

        Iterates all datasources returned by the user's Grafana instance and
        picks the best one matching each type (loki, tempo, prometheus).
        Selection priority:
        1. Datasource marked ``isDefault``
        2. Datasource whose uid/name contains a primary hint (e.g. "logs" for loki)
        3. Datasource whose uid/name does NOT contain deprioritized keywords
        4. First datasource of that type (fallback)

        Returns:
            Dict with keys loki_uid, tempo_uid, mimir_uid (only present if found).
        """
        if not self.instance_url or not self.is_configured:
            return {}

        url = f"{self.instance_url}/api/datasources"
        try:
            datasources = cast(list[dict[str, Any]], self._make_get_request(url))

            # Collect all candidates per type, then pick the best one.
            candidates: dict[str, list[dict]] = {key: [] for key in self._TYPE_MAP.values()}

            for ds in datasources:
                ds_type = ds.get("type", "").lower()
                uid = ds.get("uid", "")
                name = ds.get("name", "")
                is_default = bool(ds.get("isDefault"))
                if not uid:
                    continue

                for type_keyword, result_key in self._TYPE_MAP.items():
                    if type_keyword in ds_type:
                        candidates[result_key].append(
                            {
                                "uid": uid,
                                "name": name,
                                "is_default": is_default,
                            }
                        )
                        break

            result: dict[str, str] = {}
            for result_key, ds_list in candidates.items():
                if not ds_list:
                    continue

                logger.info(
                    "[grafana] Candidates for %s: %s",
                    result_key,
                    [(d["uid"], d["name"]) for d in ds_list],
                )

                def _is_deprioritized(d: dict) -> bool:
                    return any(
                        kw in d["uid"].lower() or kw in d["name"].lower()
                        for kw in self._DEPRIORITIZE_KEYWORDS
                    )

                # 1. Prefer the default datasource for this type
                defaults = [d for d in ds_list if d["is_default"]]
                if defaults:
                    result[result_key] = defaults[0]["uid"]
                    continue

                # Filter out deprioritized datasources for hint matching
                non_deprioritized = [d for d in ds_list if not _is_deprioritized(d)]

                # 2. Prefer non-deprioritized datasources matching primary hints
                hints = self._PRIMARY_HINTS.get(result_key, [])
                if hints and non_deprioritized:
                    hinted = [
                        d
                        for d in non_deprioritized
                        if any(h in d["uid"].lower() or h in d["name"].lower() for h in hints)
                    ]
                    if hinted:
                        result[result_key] = hinted[0]["uid"]
                        continue

                # 3. Use any non-deprioritized datasource
                if non_deprioritized:
                    result[result_key] = non_deprioritized[0]["uid"]
                    continue

                # 4. Fallback to first (even if deprioritized)
                result[result_key] = ds_list[0]["uid"]

            logger.info("[grafana] Discovered datasource UIDs: %s", result)
            return result
        except Exception as e:
            logger.warning("[grafana] Failed to discover datasource UIDs: %s", e)
            if _is_transport_failure(e):
                raise
            return {}

    def query_loki_label_values(self, label: str = "service_name") -> list[str]:
        """Query Loki for available values of a label."""
        if not self.loki_datasource_uid:
            return []
        url = self._build_datasource_url(
            self.loki_datasource_uid,
            f"/loki/api/v1/label/{label}/values",
        )
        try:
            data = self._make_get_request(url)
            values: list[str] = data.get("data", [])
            return values
        except Exception as e:
            logger.debug("Failed to fetch Loki label values for %s", label, exc_info=True)
            if _is_transport_failure(e):
                raise
            return []

    def query_alert_rules(self, folder: str | None = None) -> list[dict[str, Any]]:
        """Query Grafana alert rules, optionally filtered by folder title."""
        url = f"{self.instance_url}/api/ruler/grafana/api/v1/rules"
        try:
            data = self._make_get_request(url)
            rules: list[dict[str, Any]] = []
            for folder_name, groups in data.items():
                if folder and folder.lower() not in folder_name.lower():
                    continue
                for group in groups:
                    for rule in group.get("rules", []):
                        rules.append(
                            {
                                "folder": folder_name,
                                "group": group.get("name", ""),
                                "rule_name": rule.get("grafana_alert", {}).get("title", ""),
                                "condition": rule.get("grafana_alert", {}).get("condition", ""),
                                "datasource_uid": _extract_datasource_uid(rule),
                                "queries": _extract_rule_queries(rule),
                                "state": rule.get("grafana_alert", {}).get("current_state", ""),
                                "no_data_state": rule.get("grafana_alert", {}).get(
                                    "no_data_state", ""
                                ),
                            }
                        )
            return rules
        except Exception as e:
            logger.warning("[grafana] Failed to query alert rules: %s", e)
            if _is_transport_failure(e):
                raise
            return []

    def query_annotations(
        self,
        from_ts: int,
        to_ts: int,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query Grafana annotations in a time window (epoch ms), optional tag filter.

        Mirrors ``query_alert_rules``: a direct ``requests.get`` returning a list.
        ``/api/annotations`` responds with a JSON array, so ``_make_request`` (which
        returns a dict) is unsuitable here.
        """
        url = f"{self.instance_url}/api/annotations"
        params: dict[str, Any] = {
            "from": from_ts,
            "to": to_ts,
            "type": "annotation",
            "limit": limit,
        }
        if tags:
            params["tags"] = tags  # requests repeats the param once per tag
        try:
            data = cast(list[dict[str, Any]], self._make_get_request(url, params=params))
            return [_map_annotation(item) for item in data if isinstance(item, dict)]
        except Exception as e:
            logger.warning("[grafana] Failed to query annotations: %s", e)
            if _is_transport_failure(e):
                raise
            return []

    def create_annotation(
        self,
        text: str,
        tags: list[str],
        *,
        time_ms: int | None = None,
        time_end_ms: int | None = None,
        dashboard_uid: str | None = None,
    ) -> dict[str, Any]:
        """Create a Grafana annotation via POST /api/annotations.

        Uses the same auth as query_annotations (read_token or basic auth).
        Requires Editor role or higher on the Grafana instance.

        Returns ``{"success": True, "id": <annotation_id>}`` on success,
        or ``{"success": False, "error": "..."}`` on failure.
        """
        if not self.instance_url or not self.is_configured:
            return {"success": False, "error": "Grafana client not configured"}

        url = f"{self.instance_url}/api/annotations"
        body: dict[str, Any] = {"text": text, "tags": tags}
        if time_ms is not None:
            body["time"] = time_ms
        if time_end_ms is not None:
            body["timeEnd"] = time_end_ms
        if dashboard_uid:
            body["dashboardUID"] = dashboard_uid

        try:
            data = self._make_post_request(url, json_body=body)
            return {"success": True, "id": data.get("id")}
        except Exception as e:
            logger.warning("[grafana] Failed to create annotation: %s", e)
            return {"success": False, "error": str(e)}

    def _get_auth_headers(self) -> dict[str, str]:
        if self.username and self.password:
            credentials = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return {"Authorization": f"Basic {credentials}"}
        if not self.read_token:
            return {}
        return {"Authorization": f"Bearer {self.read_token}"}

    def _make_get_request(
        self,
        url: str,
        params: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        response = requests.get(
            url,
            headers=self._get_auth_headers(),
            params=params,
            timeout=timeout,
            verify=self._config.ssl_verify,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def _make_post_request(
        self,
        url: str,
        json_body: dict[str, Any] | list[Any] | None = None,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        """POST JSON to a Grafana/Loki API endpoint. Returns parsed JSON
        response body if present else returns an empty dict when the server
        responds with 204 No Content or an empty body.
        """
        headers = {**self._get_auth_headers(), **(extra_headers or {})}
        headers.setdefault("Content-Type", "application/json")
        response = requests.post(
            url,
            headers=headers,
            json=json_body,
            timeout=timeout,
            verify=self._config.ssl_verify,
        )
        response.raise_for_status()
        if response.status_code == HTTPStatus.NO_CONTENT or not response.content.strip():
            return {}
        data: dict[str, Any] = response.json()
        return data
