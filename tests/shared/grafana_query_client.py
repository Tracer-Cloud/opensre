"""
Shared Grafana query client for validation scripts.

Provides common functions for querying Grafana Cloud (Loki, Tempo, Mimir).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests


@dataclass
class GrafanaSecrets:
    """Container for Grafana Cloud credentials."""

    hosted_logs_id: str
    hosted_logs_url: str
    hosted_metrics_id: str
    hosted_metrics_url: str
    rw_api_key: str
    otlp_endpoint: str
    otlp_auth_header: str

    @classmethod
    def from_aws_secrets_manager(cls, secret_name: str = "tracer/grafana-cloud") -> GrafanaSecrets:
        """Load secrets from AWS Secrets Manager."""
        import boto3

        secrets_client = boto3.client("secretsmanager")
        response = secrets_client.get_secret_value(SecretId=secret_name)
        secrets = json.loads(response["SecretString"])

        return cls(
            hosted_logs_id=secrets.get("GCLOUD_HOSTED_LOGS_ID", ""),
            hosted_logs_url=secrets.get("GCLOUD_HOSTED_LOGS_URL", ""),
            hosted_metrics_id=secrets.get("GCLOUD_HOSTED_METRICS_ID", ""),
            hosted_metrics_url=secrets.get("GCLOUD_HOSTED_METRICS_URL", ""),
            rw_api_key=secrets.get("GCLOUD_RW_API_KEY", ""),
            otlp_endpoint=secrets.get("GCLOUD_OTLP_ENDPOINT", ""),
            otlp_auth_header=secrets.get("GCLOUD_OTLP_AUTH_HEADER", ""),
        )

    @classmethod
    def from_env(cls) -> GrafanaSecrets:
        """Load secrets from environment variables."""
        return cls(
            hosted_logs_id=os.getenv("GCLOUD_HOSTED_LOGS_ID", ""),
            hosted_logs_url=os.getenv("GCLOUD_HOSTED_LOGS_URL", ""),
            hosted_metrics_id=os.getenv("GCLOUD_HOSTED_METRICS_ID", ""),
            hosted_metrics_url=os.getenv("GCLOUD_HOSTED_METRICS_URL", ""),
            rw_api_key=os.getenv("GCLOUD_RW_API_KEY", ""),
            otlp_endpoint=os.getenv("GCLOUD_OTLP_ENDPOINT", ""),
            otlp_auth_header=os.getenv("GCLOUD_OTLP_AUTH_HEADER", ""),
        )

    @classmethod
    def load(cls, secret_name: str = "tracer/grafana-cloud") -> GrafanaSecrets:
        """Load secrets from AWS Secrets Manager, falling back to env vars."""
        try:
            return cls.from_aws_secrets_manager(secret_name)
        except Exception:
            return cls.from_env()


class GrafanaCloudClient:
    """Client for querying Grafana Cloud APIs (Loki, Tempo, Mimir)."""

    def __init__(self, secrets: GrafanaSecrets):
        self.secrets = secrets

    def query_loki(
        self,
        query: str,
        *,
        limit: int = 100,
        start_minutes_ago: int = 5,
    ) -> list[dict[str, Any]]:
        """Query Grafana Cloud Loki for logs."""
        logs_url = self.secrets.hosted_logs_url.replace(
            "/loki/api/v1/push", "/loki/api/v1/query_range"
        )

        end = datetime.now(UTC)
        start = end - timedelta(minutes=start_minutes_ago)

        try:
            response = requests.get(
                logs_url,
                params={
                    "query": query,
                    "limit": limit,
                    "start": int(start.timestamp() * 1e9),
                    "end": int(end.timestamp() * 1e9),
                },
                auth=(self.secrets.hosted_logs_id, self.secrets.rw_api_key),
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("result", [])
        except requests.RequestException as e:
            print(f"Loki query failed: {e}")

        return []

    def query_tempo(
        self,
        service_name: str,
        *,
        limit: int = 10,
        execution_run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query Grafana Cloud Tempo for traces."""
        # Try to get read token from env or .env file
        read_token = os.getenv("GRAFANA_READ_TOKEN")
        if not read_token:
            env_path = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        if line.startswith("GRAFANA_READ_TOKEN="):
                            read_token = line.split("=", 1)[1].strip().strip('"')
                            break

        if not read_token:
            print("No GRAFANA_READ_TOKEN found")
            return []

        grafana_instance = "https://tracerbio.grafana.net"
        trace_search_url = f"{grafana_instance}/api/datasources/proxy/uid/grafanacloud-traces/api/search"

        query = f'{{.service.name="{service_name}"}}'
        if execution_run_id:
            query = f'{{.service.name="{service_name}" && resource.execution.run_id="{execution_run_id}"}}'

        try:
            response = requests.get(
                trace_search_url,
                headers={"Authorization": f"Bearer {read_token}"},
                params={"q": query, "limit": limit},
                timeout=10,
            )
            if response.status_code == 200:
                return response.json().get("traces", [])
        except requests.RequestException as e:
            print(f"Tempo query failed: {e}")

        return []

    def query_mimir(
        self,
        query: str,
    ) -> list[dict[str, Any]]:
        """Query Grafana Cloud Mimir for metrics."""
        metrics_url = self.secrets.hosted_metrics_url.replace(
            "/api/prom/push", "/api/prom/api/v1/query"
        )

        try:
            response = requests.get(
                metrics_url,
                params={"query": query},
                auth=(self.secrets.hosted_metrics_id, self.secrets.rw_api_key),
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("result", [])
        except requests.RequestException as e:
            print(f"Mimir query failed: {e}")

        return []


