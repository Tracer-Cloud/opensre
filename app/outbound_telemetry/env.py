from __future__ import annotations

import os

from config.grafana_config import load_env


def parse_otel_headers(headers_str: str | None = None) -> dict[str, str]:
    headers_raw = headers_str if headers_str is not None else os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")
    headers: dict[str, str] = {}
    if headers_raw:
        for pair in headers_raw.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    return headers
