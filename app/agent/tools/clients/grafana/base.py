"""Base HTTP client for Grafana Cloud API."""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import requests

from app.agent.tools.clients.grafana.config import GrafanaAccountConfig

logger = logging.getLogger(__name__)


def _extract_datasource_uid(rule: dict) -> str:
    """Extract the primary datasource UID from an alert rule."""
    alert = rule.get("grafana_alert", {})
    for datum in alert.get("data", []):
        model = datum.get("model", {})
        ds = model.get("datasource", {})
        if ds.get("uid"):
            return ds["uid"]
    return ""


def _extract_rule_queries(rule: dict) -> list[dict]:
    """Extract query expressions from an alert rule."""
    alert = rule.get("grafana_alert", {})
    queries = []
    for datum in alert.get("data", []):
        model = datum.get("model", {})
        expr = model.get("expr", "")
        if expr:
            queries.append({
                "ref_id": datum.get("refId", ""),
                "expr": expr,
                "datasource_uid": model.get("datasource", {}).get("uid", ""),
            })
    return queries


class GrafanaClientBase:
    """Base HTTP client with common request methods for Grafana Cloud."""

    def __init__(self, config: GrafanaAccountConfig):
        self._config = config
        self.account_id = config.account_id
        self.instance_url = config.instance_url
        self.read_token = config.read_token
        self.loki_datasource_uid = config.loki_datasource_uid
        self.tempo_datasource_uid = config.tempo_datasource_uid
        self.mimir_datasource_uid = config.mimir_datasource_uid

    @property
    def is_configured(self) -> bool:
        return self._config.is_configured

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

    def discover_datasource_uids(self) -> dict[str, str]:
        """Discover datasource UIDs by querying GET /api/datasources.

        Iterates all datasources returned by the user's Grafana instance and
        picks the first one matching each type (loki, tempo, prometheus).
        No hardcoded UIDs — everything is discovered from the API.

        Returns:
            Dict with keys loki_uid, tempo_uid, mimir_uid (only present if found).
        """
        if not self.instance_url or not self.read_token:
            return {}

        url = f"{self.instance_url}/api/datasources"
        try:
            response = requests.get(
                url,
                headers=self._get_auth_headers(),
                timeout=10,
            )
            response.raise_for_status()
            datasources = response.json()

            result: dict[str, str] = {}
            for ds in datasources:
                ds_type = ds.get("type", "").lower()
                uid = ds.get("uid", "")
                if not uid:
                    continue

                for type_keyword, result_key in self._TYPE_MAP.items():
                    if type_keyword in ds_type and result_key not in result:
                        result[result_key] = uid
                        break

                # Stop early if all three found
                if len(result) == len(self._TYPE_MAP):
                    break

            logger.info("[grafana] Discovered datasource UIDs: %s", result)
            return result
        except Exception as e:
            logger.warning("[grafana] Failed to discover datasource UIDs: %s", e)
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
            data = self._make_request(url)
            return data.get("data", [])
        except Exception:
            return []

    def query_alert_rules(self, folder: str | None = None) -> list[dict[str, Any]]:
        """Query Grafana alert rules, optionally filtered by folder title."""
        url = f"{self.instance_url}/api/ruler/grafana/api/v1/rules"
        try:
            response = requests.get(
                url,
                headers=self._get_auth_headers(),
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            rules: list[dict[str, Any]] = []
            for folder_name, groups in data.items():
                if folder and folder.lower() not in folder_name.lower():
                    continue
                for group in groups:
                    for rule in group.get("rules", []):
                        rules.append({
                            "folder": folder_name,
                            "group": group.get("name", ""),
                            "rule_name": rule.get("grafana_alert", {}).get("title", ""),
                            "condition": rule.get("grafana_alert", {}).get("condition", ""),
                            "datasource_uid": _extract_datasource_uid(rule),
                            "queries": _extract_rule_queries(rule),
                            "state": rule.get("grafana_alert", {}).get("current_state", ""),
                            "no_data_state": rule.get("grafana_alert", {}).get("no_data_state", ""),
                        })
            return rules
        except Exception as e:
            logger.warning("[grafana] Failed to query alert rules: %s", e)
            return []

    def _get_auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_token}"}

    def _make_request(
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
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result
