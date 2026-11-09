"""Grafana integration verifier — datasource discovery probe."""

from __future__ import annotations

from typing import Any, cast

import requests

from integrations.config_models import GrafanaIntegrationConfig
from integrations.verification import register_verifier, result

_SUPPORTED_GRAFANA_TYPES = ("loki", "tempo", "prometheus")


def _discover_datasources(config: GrafanaIntegrationConfig) -> list[Any] | str:
    """Call ``GET /api/datasources``.

    Returns the parsed JSON payload on success, or a human-readable error
    string describing the failure (HTTP status + body, or a transport error).
    """
    url = f"{config.endpoint.rstrip('/')}/api/datasources"
    try:
        response = requests.get(
            url,
            headers=config.auth_headers,
            timeout=10,
            verify=config.ssl_verify,
        )
        response.raise_for_status()
        return cast(list[Any], response.json())
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            body = exc.response.text.strip().replace("\n", " ")[:200]
        detail = f"Datasource discovery failed: HTTP {status} from {url}"
        return f"{detail}: {body}" if body else detail
    except requests.RequestException as exc:
        # Keep DETAIL short for the verify table — the full HTTPConnectionPool
        # dump overflows even with fold, and the endpoint + exception type
        # are enough to diagnose unreachable Grafana.
        kind = type(exc).__name__
        return f"Datasource discovery failed: could not reach {config.endpoint} ({kind})"
    except Exception as exc:
        return f"Datasource discovery failed: {type(exc).__name__}: {exc}"


@register_verifier("grafana")
def verify_grafana(source: str, config: dict[str, Any]) -> dict[str, str]:
    """Verify a Grafana instance (cloud or local) by discovering its datasources.

    Authentication is delegated to :class:`GrafanaIntegrationConfig`; this
    verifier only decides whether enough credentials exist, performs the probe,
    and turns the outcome into a user-facing verification result.
    """
    try:
        grafana_config = GrafanaIntegrationConfig.model_validate(config)
    except Exception as err:
        return result("grafana", source, "missing", str(err))

    if not grafana_config.endpoint:
        return result("grafana", source, "missing", "Missing Grafana endpoint URL.")
    if not grafana_config.has_usable_credentials:
        return result(
            "grafana",
            source,
            "missing",
            "Missing Grafana credentials: provide a service account token or username/password.",
        )

    payload = _discover_datasources(grafana_config)
    if isinstance(payload, str):
        return result("grafana", source, "failed", payload)

    datasources = payload if isinstance(payload, list) else []
    supported_types = sorted(
        {
            datasource_type
            for datasource in datasources
            for datasource_type in [str(datasource.get("type", "")).lower()]
            if any(keyword in datasource_type for keyword in _SUPPORTED_GRAFANA_TYPES)
        }
    )
    if not supported_types:
        return result(
            "grafana",
            source,
            "failed",
            "Connected, but no Loki, Tempo, or Prometheus datasources were discovered.",
        )

    return result(
        "grafana",
        source,
        "passed",
        f"Connected to {grafana_config.endpoint} and discovered "
        f"{', '.join(supported_types)} datasources.",
    )
