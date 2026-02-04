from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import requests
from requests.auth import HTTPBasicAuth

from config.grafana_config import (
    get_hosted_logs_id,
    get_hosted_logs_url,
    get_hosted_metrics_id,
    get_hosted_metrics_url,
    get_hosted_traces_id,
    get_hosted_traces_url,
    get_rw_api_key,
    load_env,
)


@dataclass(frozen=True)
class GrafanaCloudConfig:
    hosted_logs_id: str
    hosted_logs_url: str
    hosted_metrics_id: str
    hosted_metrics_url: str
    hosted_traces_id: str
    hosted_traces_url: str
    rw_api_key: str

    @property
    def is_configured(self) -> bool:
        return bool(
            self.hosted_logs_id
            and self.hosted_logs_url
            and self.hosted_metrics_id
            and self.hosted_metrics_url
            and self.hosted_traces_id
            and self.hosted_traces_url
            and self.rw_api_key
        )

    @classmethod
    def from_env(cls) -> GrafanaCloudConfig:
        load_env()
        return cls(
            hosted_logs_id=get_hosted_logs_id(),
            hosted_logs_url=get_hosted_logs_url(),
            hosted_metrics_id=get_hosted_metrics_id(),
            hosted_metrics_url=get_hosted_metrics_url(),
            hosted_traces_id=get_hosted_traces_id(),
            hosted_traces_url=get_hosted_traces_url(),
            rw_api_key=get_rw_api_key(),
        )


class GrafanaCloudClient:
    def __init__(self, config: GrafanaCloudConfig):
        self.config = config

    @classmethod
    def from_env(cls) -> GrafanaCloudClient:
        return cls(GrafanaCloudConfig.from_env())

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

    def _mimir_query_url(self) -> str:
        return self.config.hosted_metrics_url.replace(
            "/api/prom/push", "/api/prom/api/v1/query"
        )

    def _loki_query_range_url(self) -> str:
        return self.config.hosted_logs_url.replace(
            "/loki/api/v1/push", "/loki/api/v1/query_range"
        )

    def _tempo_search_url(self) -> str:
        return self.config.hosted_traces_url.rstrip("/") + "/api/search"

    def _basic_auth(self, hosted_id: str) -> HTTPBasicAuth:
        return HTTPBasicAuth(hosted_id, self.config.rw_api_key)

    def query_mimir(self, query: str, *, timeout: int = 10) -> dict[str, Any]:
        response = requests.get(
            self._mimir_query_url(),
            params={"query": query},
            auth=self._basic_auth(self.config.hosted_metrics_id),
            timeout=timeout,
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def query_loki(
        self,
        query: str,
        *,
        start_ns: str,
        end_ns: str,
        limit: int = 100,
        timeout: int = 10,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "query": query,
            "limit": limit,
            "start": start_ns,
            "end": end_ns,
        }
        response = requests.get(
            self._loki_query_range_url(),
            params=params,
            auth=self._basic_auth(self.config.hosted_logs_id),
            timeout=timeout,
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def query_tempo(
        self,
        service_name: str,
        *,
        start_s: int,
        end_s: int,
        limit: int = 20,
        timeout: int = 10,
    ) -> dict[str, Any]:
        params: dict[str, str | int] = {
            "limit": limit,
            "start": start_s,
            "end": end_s,
            "q": f'{{resource.service.name="{service_name}"}}',
        }
        response = requests.get(
            self._tempo_search_url(),
            params=params,
            auth=self._basic_auth(self.config.hosted_traces_id),
            timeout=timeout,
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())
