"""
Stateless LangGraph Studio client utilities.

Pure functions for interacting with LangGraph Studio API.
Handles endpoint selection (local vs remote) internally.
"""

from typing import Any

import requests

from tests.conftest import LANGGRAPH_LOCAL_ENDPOINT, LANGGRAPH_REMOTE_ENDPOINT


def _select_endpoint() -> str:
    """Try local first, fall back to remote."""
    try:
        requests.get(LANGGRAPH_LOCAL_ENDPOINT.replace("/runs/stream", ""), timeout=1)
        return LANGGRAPH_LOCAL_ENDPOINT
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return LANGGRAPH_REMOTE_ENDPOINT


def _post_alert(
    endpoint: str,
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
    config_metadata: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
    timeout: int = 300,
) -> requests.Response:
    """Internal helper to post alert to a specific endpoint."""
    payload = {
        "input": {
            "alert_name": alert_name,
            "pipeline_name": pipeline_name,
            "severity": severity,
            "raw_alert": raw_alert,
        },
        "config": {"metadata": config_metadata or {}},
        "stream_mode": stream_mode or ["values"],
    }

    response = requests.post(endpoint, json=payload, stream=True, timeout=timeout)
    response.raise_for_status()
    return response


def fire_alert_to_remote_langgraph_client(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
    config_metadata: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
    timeout: int = 300,
) -> requests.Response:
    """
    Fire alert specifically to the REMOTE LangGraph client.

    Args:
        alert_name: Name of the alert
        pipeline_name: Name of the pipeline
        severity: Alert severity (e.g., "critical", "warning")
        raw_alert: Raw alert payload
        config_metadata: Optional metadata to include in config
        stream_mode: Stream mode (default: ["values"])
        timeout: Request timeout in seconds

    Returns:
        requests.Response object (for streaming)
    """
    return _post_alert(
        endpoint=LANGGRAPH_REMOTE_ENDPOINT,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=raw_alert,
        config_metadata=config_metadata,
        stream_mode=stream_mode,
        timeout=timeout,
    )


def fire_alert_to_langgraph(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict[str, Any],
    config_metadata: dict[str, Any] | None = None,
    stream_mode: list[str] | None = None,
    timeout: int = 300,
) -> requests.Response:
    """
    Fire alert to LangGraph (tries local, falls back to remote).

    Args:
        alert_name: Name of the alert
        pipeline_name: Name of the pipeline
        severity: Alert severity (e.g., "critical", "warning")
        raw_alert: Raw alert payload (dict with error details, CloudWatch links, etc.)
        config_metadata: Optional metadata to include in config
        stream_mode: Stream mode (default: ["values"])
        timeout: Request timeout in seconds

    Returns:
        requests.Response object (for streaming)

    Raises:
        requests.exceptions.HTTPError: If request fails
    """
    endpoint = _select_endpoint()
    return _post_alert(
        endpoint=endpoint,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=raw_alert,
        config_metadata=config_metadata,
        stream_mode=stream_mode,
        timeout=timeout,
    )


def stream_investigation_results(response: requests.Response) -> None:
    """
    Stream and print investigation results from LangGraph API response.

    Args:
        response: requests.Response object from fire_alert_to_langgraph
    """
    print("\nStreaming investigation:\n")

    for line in response.iter_lines():
        if line:
            decoded = line.decode("utf-8")
            if decoded.strip():
                print(decoded)
