#!/usr/bin/env python3
"""Wait for the local LangGraph dev server to become ready."""

import sys
import time
import urllib.error
import urllib.request

HEALTH_URL = "http://localhost:2024/ok"
TIMEOUT_SECONDS = 30
POLL_INTERVAL = 1


def wait_for_server() -> None:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(HEALTH_URL, timeout=2)
            print("LangGraph server is ready.")
            return
        except (urllib.error.URLError, OSError):
            time.sleep(POLL_INTERVAL)

    print(f"LangGraph server did not become ready within {TIMEOUT_SECONDS}s.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    wait_for_server()
