from __future__ import annotations

import pytest

from config.grafana_config import (
    get_account_instance_url,
    get_account_read_token,
    get_hosted_logs_id,
    get_hosted_logs_url,
    get_hosted_metrics_id,
    get_hosted_metrics_url,
    get_otlp_auth_header,
    get_otlp_endpoint,
    get_rw_api_key,
    load_env,
)


def _skip_if_missing(missing: list[str], *, reason: str) -> None:
    if missing:
        pytest.skip(f"{reason}; missing env vars: {', '.join(missing)}")


def require_grafana_cloud_env() -> None:
    load_env()
    required = {
        "GCLOUD_OTLP_ENDPOINT": get_otlp_endpoint(),
        "GCLOUD_OTLP_AUTH_HEADER": get_otlp_auth_header(),
        "GCLOUD_HOSTED_METRICS_ID": get_hosted_metrics_id(),
        "GCLOUD_HOSTED_METRICS_URL": get_hosted_metrics_url(),
        "GCLOUD_HOSTED_LOGS_ID": get_hosted_logs_id(),
        "GCLOUD_HOSTED_LOGS_URL": get_hosted_logs_url(),
        "GCLOUD_RW_API_KEY": get_rw_api_key(),
    }
    missing = [key for key, value in required.items() if not value]
    _skip_if_missing(missing, reason="Grafana Cloud telemetry not configured")


def require_grafana_query_env(account_id: str | None = None) -> None:
    load_env()
    normalized_id = (account_id or "tracerbio").lower()
    token_key = (
        "GRAFANA_READ_TOKEN"
        if normalized_id == "tracerbio"
        else f"GRAFANA_{normalized_id.upper()}_READ_TOKEN"
    )
    instance_key = (
        "GRAFANA_INSTANCE_URL"
        if normalized_id == "tracerbio"
        else f"GRAFANA_{normalized_id.upper()}_INSTANCE_URL"
    )
    required = {
        token_key: get_account_read_token(normalized_id),
        instance_key: get_account_instance_url(normalized_id),
    }
    missing = [key for key, value in required.items() if not value]
    _skip_if_missing(missing, reason="Grafana query tests require configured access")
