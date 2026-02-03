"""
Shared Grafana validation utilities for test cases.

Provides common functions for querying Loki logs and Tempo traces,
and validating that telemetry appears in Grafana.
"""

import requests

DEFAULT_GRAFANA_URL = "http://localhost:3000"


def get_grafana_urls(grafana_url: str = DEFAULT_GRAFANA_URL) -> tuple[str, str]:
    """Get Loki and Tempo URLs from base Grafana URL."""
    loki_url = f"{grafana_url}/loki/api/v1"
    tempo_url = f"{grafana_url}/api/tempo/api/traces"
    return loki_url, tempo_url


def check_grafana_health(grafana_url: str = DEFAULT_GRAFANA_URL) -> bool:
    """Check if Grafana is running and healthy."""
    try:
        response = requests.get(f"{grafana_url}/api/health", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def query_loki_logs(
    execution_run_id: str,
    service_name: str,
    grafana_url: str = DEFAULT_GRAFANA_URL,
) -> list[dict]:
    """Query Loki for logs containing execution_run_id."""
    loki_url, _ = get_grafana_urls(grafana_url)
    try:
        query = f'{{service_name="{service_name}"}} |= "{execution_run_id}"'
        response = requests.get(
            f"{loki_url}/query",
            params={"query": query, "limit": 100},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        print(f"Loki query failed: {e}")
    return []


def query_tempo_traces(
    execution_run_id: str,
    grafana_url: str = DEFAULT_GRAFANA_URL,
) -> list[dict]:
    """Query Tempo for traces with execution.run_id attribute."""
    _, tempo_url = get_grafana_urls(grafana_url)
    try:
        query = f'{{execution.run_id="{execution_run_id}"}}'
        response = requests.get(
            f"{tempo_url}/search",
            params={"tags": query},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json().get("traces", [])
    except requests.RequestException as e:
        print(f"Tempo query failed: {e}")
    return []


class GrafanaValidator:
    """Validates telemetry data in Grafana for a specific service."""

    def __init__(
        self,
        service_name: str,
        grafana_url: str = DEFAULT_GRAFANA_URL,
        expected_spans: list[str] | None = None,
        require_logs: bool = True,
    ):
        self.service_name = service_name
        self.grafana_url = grafana_url
        self.expected_spans = expected_spans
        self.require_logs = require_logs

    def check_health(self) -> bool:
        """Check if Grafana is healthy."""
        return check_grafana_health(self.grafana_url)

    def query_logs(self, execution_run_id: str) -> list[dict]:
        """Query logs for the configured service."""
        return query_loki_logs(execution_run_id, self.service_name, self.grafana_url)

    def query_traces(self, execution_run_id: str) -> list[dict]:
        """Query traces by execution run ID."""
        return query_tempo_traces(execution_run_id, self.grafana_url)

    def validate(self, execution_run_id: str, verbose: bool = True) -> bool:
        """Validate that telemetry appears in Grafana."""
        if verbose:
            print(f"\nValidating Grafana telemetry for execution_run_id={execution_run_id}...")

        logs = self.query_logs(execution_run_id)
        traces = self.query_traces(execution_run_id)

        if verbose:
            print(f"  Logs found: {len(logs)}")
            print(f"  Traces found: {len(traces)}")

            if traces and self.expected_spans:
                print("  Expected spans:")
                for span in self.expected_spans:
                    print(f"    - {span}")

        # Determine success criteria
        traces_ok = len(traces) > 0
        logs_ok = len(logs) > 0 if self.require_logs else True

        if traces_ok and logs_ok:
            if verbose:
                print("✓ Telemetry validation passed")
            return True
        else:
            if verbose:
                print("✗ Telemetry validation failed")
            return False

    def require_grafana_running(self) -> bool:
        """Check that Grafana is running, print error if not."""
        if not self.check_health():
            print(f"✗ Grafana is not running on {self.grafana_url}")
            print("  Run: make grafana-local")
            return False
        return True
