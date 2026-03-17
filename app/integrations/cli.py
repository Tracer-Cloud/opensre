"""Interactive CLI for managing local integrations (~/.tracer/integrations.json).

Usage:
    python -m app.integrations setup <service>
    python -m app.integrations list
    python -m app.integrations show <service>
    python -m app.integrations remove <service>

Supported services: aws, grafana, datadog, opensearch, rds, tracer
"""

from __future__ import annotations

import getpass
import json
import sys
from typing import Any

from app.integrations.store import (
    STORE_PATH,
    get_integration,
    list_integrations,
    remove_integration,
    upsert_integration,
)

_B = "\033[1m"
_R = "\033[0m"
_SECRET_KEYS = frozenset({"api_key", "app_key", "password", "secret_access_key", "session_token", "jwt_token"})


def _p(label: str, default: str = "", secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    prompt = f"  {_B}{label}{_R}{hint}: "
    try:
        value = getpass.getpass(prompt) if secret else input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    return value or default


def _die(msg: str) -> None:
    print(f"  error: {msg}", file=sys.stderr)
    sys.exit(1)


def _mask(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: (v[:4] + "****" if isinstance(v, str) and v else "****") if k in _SECRET_KEYS else _mask(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(i) for i in obj]
    return obj


# ─── setup flows ──────────────────────────────────────────────────────────────

def _setup_grafana() -> None:
    endpoint = _p("Instance URL (e.g. https://myorg.grafana.net)")
    api_key = _p("Service account token", secret=True)
    if not endpoint or not api_key:
        _die("endpoint and api_key are required.")
    upsert_integration("grafana", {"credentials": {"endpoint": endpoint, "api_key": api_key}})


def _setup_datadog() -> None:
    api_key = _p("API key", secret=True)
    app_key = _p("Application key", secret=True)
    site = _p("Site", default="datadoghq.com")
    if not api_key or not app_key:
        _die("api_key and app_key are required.")
    upsert_integration("datadog", {"credentials": {"api_key": api_key, "app_key": app_key, "site": site}})


def _setup_aws() -> None:
    print("  1) IAM Role ARN  2) Access Key + Secret")
    choice = _p("Choice", default="1")
    region = _p("Region", default="us-east-1")
    if choice == "1":
        role_arn = _p("IAM Role ARN")
        if not role_arn:
            _die("role_arn is required.")
        upsert_integration("aws", {"role_arn": role_arn, "external_id": _p("External ID (optional)"), "credentials": {"region": region}})
    else:
        access_key = _p("AWS_ACCESS_KEY_ID", secret=True)
        secret_key = _p("AWS_SECRET_ACCESS_KEY", secret=True)
        if not access_key or not secret_key:
            _die("access_key and secret_key are required.")
        upsert_integration("aws", {"credentials": {"access_key_id": access_key, "secret_access_key": secret_key, "session_token": _p("Session token (optional)"), "region": region}})


def _setup_opensearch() -> None:
    endpoint = _p("Endpoint (e.g. https://my-cluster.us-east-1.es.amazonaws.com)")
    print("  1) Username + Password  2) API key")
    creds: dict[str, Any] = {"endpoint": endpoint}
    if _p("Choice", default="1") == "2":
        creds["api_key"] = _p("API key", secret=True)
    else:
        creds["username"] = _p("Username", default="admin")
        creds["password"] = _p("Password", secret=True)
    upsert_integration("opensearch", {"credentials": creds})


def _setup_rds() -> None:
    host = _p("Host (e.g. mydb.xxxx.us-east-1.rds.amazonaws.com)")
    port = _p("Port", default="5432")
    database = _p("Database name")
    username = _p("Username")
    password = _p("Password", secret=True)
    if not host or not database or not username:
        _die("host, database, and username are required.")
    upsert_integration("rds", {"credentials": {"host": host, "port": int(port) if port.isdigit() else 5432, "database": database, "username": username, "password": password}})


def _setup_tracer() -> None:
    base_url = _p("Tracer web app URL", default="http://localhost:3000")
    jwt_token = _p("JWT token", secret=True)
    if not base_url or not jwt_token:
        _die("base_url and jwt_token are required.")
    upsert_integration("tracer", {"credentials": {"base_url": base_url, "jwt_token": jwt_token}})


_HANDLERS: dict[str, Any] = {
    "aws": _setup_aws,
    "datadog": _setup_datadog,
    "grafana": _setup_grafana,
    "opensearch": _setup_opensearch,
    "rds": _setup_rds,
    "tracer": _setup_tracer,
}

SUPPORTED = ", ".join(_HANDLERS)



def cmd_setup(service: str | None) -> None:
    if not service or service not in _HANDLERS:
        _die(f"Usage: setup <service>. Supported: {SUPPORTED}")
        return
    print(f"\n  Setting up {_B}{service}{_R}\n")
    _HANDLERS[service]()
    print(f"\n  ✓ Saved → {STORE_PATH}\n")


def cmd_list() -> None:
    items = list_integrations()
    if not items:
        print("  No integrations. Run: python -m app.integrations setup <service>")
        return
    print(f"\n  {_B}{'SERVICE':<14}STATUS    ID{_R}")
    for i in items:
        print(f"  {i['service']:<14}{i['status']:<10}{i['id']}")
    print()


def cmd_show(service: str | None) -> None:
    if not service:
        _die("Usage: show <service>")
        return
    record = get_integration(service)
    if not record:
        _die(f"No active integration for '{service}'.")
        return
    print(json.dumps(_mask(record), indent=2))


def cmd_remove(service: str | None) -> None:
    if not service:
        _die("Usage: remove <service>")
        return
    try:
        confirm = input(f"  Remove '{service}'? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if confirm not in ("y", "yes"):
        print("  Cancelled.")
        return
    if remove_integration(service):
        print(f"  ✓ Removed '{service}'.")
    else:
        print(f"  No integration found for '{service}'.")
