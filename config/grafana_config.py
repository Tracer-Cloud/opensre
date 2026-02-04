from __future__ import annotations

import os
from pathlib import Path

DEFAULT_INSTANCE_URL = "https://tracerbio.grafana.net"
DEFAULT_LOKI_UID = "grafanacloud-logs"
DEFAULT_TEMPO_UID = "grafanacloud-traces"
DEFAULT_MIMIR_UID = "grafanacloud-prom"


def load_env(env_path: Path | str | None = None) -> None:
    if os.getenv("GRAFANA_CONFIG_SKIP_ENV_FILE") == "1":
        return
    if env_path is None:
        env_path = Path.cwd() / ".env"
    path = Path(env_path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_account_read_token(account_id: str) -> str:
    load_env()
    if account_id == "tracerbio":
        return get_grafana_read_token()
    return os.getenv(f"GRAFANA_{account_id.upper()}_READ_TOKEN", "")


def get_account_instance_url(account_id: str) -> str:
    load_env()
    if account_id == "tracerbio":
        return get_grafana_instance_url()
    return os.getenv(f"GRAFANA_{account_id.upper()}_INSTANCE_URL", "")


def get_account_datasource_uids(account_id: str) -> tuple[str, str, str]:
    load_env()
    if account_id == "tracerbio":
        return get_datasource_uids()
    prefix = f"GRAFANA_{account_id.upper()}"
    loki_uid = os.getenv(f"{prefix}_LOKI_DATASOURCE_UID", DEFAULT_LOKI_UID)
    tempo_uid = os.getenv(f"{prefix}_TEMPO_DATASOURCE_UID", DEFAULT_TEMPO_UID)
    mimir_uid = os.getenv(f"{prefix}_MIMIR_DATASOURCE_UID", DEFAULT_MIMIR_UID)
    return loki_uid, tempo_uid, mimir_uid


def list_account_ids() -> list[str]:
    load_env()
    accounts = {"tracerbio"}
    for key in os.environ:
        if key.startswith("GRAFANA_") and key.endswith("_READ_TOKEN"):
            account_id = key[len("GRAFANA_") : -len("_READ_TOKEN")].lower()
            if account_id:
                accounts.add(account_id)
    return sorted(accounts)


def get_grafana_read_token() -> str:
    load_env()
    return os.getenv("GRAFANA_READ_TOKEN", "")


def get_grafana_instance_url() -> str:
    load_env()
    return os.getenv("GRAFANA_INSTANCE_URL", DEFAULT_INSTANCE_URL)


def get_datasource_uids() -> tuple[str, str, str]:
    load_env()
    loki_uid = os.getenv("GRAFANA_LOKI_DATASOURCE_UID", DEFAULT_LOKI_UID)
    tempo_uid = os.getenv("GRAFANA_TEMPO_DATASOURCE_UID", DEFAULT_TEMPO_UID)
    mimir_uid = os.getenv("GRAFANA_MIMIR_DATASOURCE_UID", DEFAULT_MIMIR_UID)
    return loki_uid, tempo_uid, mimir_uid


def get_otlp_endpoint() -> str:
    load_env()
    return os.getenv("GCLOUD_OTLP_ENDPOINT", "")


def get_otlp_auth_header() -> str:
    load_env()
    return os.getenv("GCLOUD_OTLP_AUTH_HEADER", "")


def get_otel_protocol() -> str:
    load_env()
    return os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")


def get_effective_otlp_endpoint() -> str:
    load_env()
    return os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or get_otlp_endpoint()


def get_hosted_logs_id() -> str:
    load_env()
    return os.getenv("GCLOUD_HOSTED_LOGS_ID", "")


def get_hosted_logs_url() -> str:
    load_env()
    return os.getenv("GCLOUD_HOSTED_LOGS_URL", "")


def get_hosted_metrics_id() -> str:
    load_env()
    return os.getenv("GCLOUD_HOSTED_METRICS_ID", "")


def get_hosted_metrics_url() -> str:
    load_env()
    return os.getenv("GCLOUD_HOSTED_METRICS_URL", "")


def get_hosted_traces_id() -> str:
    load_env()
    return os.getenv("GCLOUD_HOSTED_TRACES_ID", "")


def get_hosted_traces_url() -> str:
    load_env()
    traces_url = os.getenv("GCLOUD_HOSTED_TRACES_URL_TEMPO") or os.getenv(
        "GCLOUD_HOSTED_TRACES_URL", ""
    )
    return traces_url


def get_rw_api_key() -> str:
    load_env()
    return os.getenv("GCLOUD_RW_API_KEY", "")


def is_grafana_otlp_endpoint(value: str | None = None) -> bool:
    endpoint = value if value is not None else get_effective_otlp_endpoint()
    return "grafana.net" in endpoint or "grafana.com" in endpoint
