"""Ancillary helpers for local testing and connectivity checks."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable

CONNECTIVITY_TIMEOUT_SECONDS = 5
CONNECTIVITY_SAMPLE_BYTES = 512


def _format_response_sample(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace").strip()
    return " ".join(text.split())


def _parse_otlp_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    raw_headers = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
    if raw_headers:
        for pair in raw_headers.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    if not headers:
        auth_header = os.getenv("GCLOUD_OTLP_AUTH_HEADER")
        if auth_header:
            headers["Authorization"] = auth_header
    return headers


def _check_http_get(
    logger,
    log_stdout: Callable[[str], None],
    label: str,
    url: str,
    headers: dict[str, str] | None = None,
    log_body: bool = False,
    log_to_stdout: bool = False,
) -> None:
    try:
        request_headers = headers or {}
        req = urllib.request.Request(url, headers=request_headers, method="GET")
        with urllib.request.urlopen(req, timeout=CONNECTIVITY_TIMEOUT_SECONDS) as response:
            body = response.read(CONNECTIVITY_SAMPLE_BYTES) if log_body else b""
            message = f"{label} GET {url} -> {response.status} {response.reason}"
            if log_body:
                message += f" response={_format_response_sample(body)}"
            logger.info(message)
            if log_to_stdout:
                log_stdout(message)

    except urllib.error.HTTPError as exc:
        body = exc.read(CONNECTIVITY_SAMPLE_BYTES) if log_body else b""
        message = f"{label} GET {url} -> {exc.code} {exc.reason}"
        if log_body:
            message += f" response={_format_response_sample(body)}"
        logger.info(message)
        if log_to_stdout:
            log_stdout(message)
    except urllib.error.URLError as exc:
        message = f"{label} GET {url} failed: {exc.reason}"
        logger.info(message)
        if log_to_stdout:
            log_stdout(message)


def run_connectivity_checks(logger, log_stdout: Callable[[str], None]) -> None:
    external_url = "https://example.com/"
    _check_http_get(
        logger,
        log_stdout,
        label="External",
        url=external_url,
    )

    grafana_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("GCLOUD_OTLP_ENDPOINT")
    if not grafana_endpoint:
        logger.info("Grafana connectivity check skipped: no OTLP endpoint configured")
        return

    _check_http_get(
        logger,
        log_stdout,
        label="Grafana",
        url=grafana_endpoint,
        headers=_parse_otlp_headers(),
        log_body=True,
        log_to_stdout=True,
    )


def run_local_flow(flow_fn) -> None:
    if len(sys.argv) == 4:
        bucket, key, processed_bucket = sys.argv[1], sys.argv[2], sys.argv[3]
        result = flow_fn(bucket, key, processed_bucket)
        print(f"Result: {result}")
    else:
        print("Usage: python main_pipeline.py <bucket> <key> <processed_bucket>")
