"""Seed one deterministic trace into the local Grafana Tempo container."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import Request, urlopen

_OTLP_HTTP_TRACES_URL = "http://127.0.0.1:4318/v1/traces"
TRACE_ID = "0123456789abcdef0123456789abcdef"


def _trace_payload(end_time_unix_nano: int) -> dict[str, Any]:
    """Build an OTLP/HTTP trace payload ending at the supplied time."""
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "checkout-service"},
                        },
                        {
                            "key": "deployment.environment",
                            "value": {"stringValue": "local"},
                        },
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "opensre.local-verification"},
                        "spans": [
                            {
                                "traceId": TRACE_ID,
                                "spanId": "0123456789abcdef",
                                "name": "POST /checkout",
                                "kind": 2,
                                "startTimeUnixNano": str(end_time_unix_nano - 2_000_000_000),
                                "endTimeUnixNano": str(end_time_unix_nano),
                                "attributes": [
                                    {
                                        "key": "http.response.status_code",
                                        "value": {"intValue": "500"},
                                    }
                                ],
                                "status": {
                                    "code": 2,
                                    "message": "seeded failure",
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def main() -> None:
    """Send one recent checkout error trace to the local OTLP endpoint."""
    request = Request(
        _OTLP_HTTP_TRACES_URL,
        data=json.dumps(_trace_payload(time.time_ns())).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        response.read()
    print(f"Seeded checkout-service trace {TRACE_ID}")


if __name__ == "__main__":
    main()
