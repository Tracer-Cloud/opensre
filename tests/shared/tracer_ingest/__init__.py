from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import find_dotenv, load_dotenv

# Load environment from .env once (does NOT override already-set env vars)
load_dotenv(find_dotenv(usecwd=True), override=False)

# ---------------------------------------------------------------------
# Debug switch:
# export TRACER_INGEST_DEBUG=1
# ---------------------------------------------------------------------
_DEBUG = os.getenv("TRACER_INGEST_DEBUG", "").strip().lower() in {"1", "true", "yes"}


def _log(msg: str) -> None:
    """Lightweight stdout logger for local debugging."""
    if _DEBUG:
        print(f"[tracer_ingest] {msg}")


def _redact_token(token: str) -> str:
    """Redact token to avoid leaking secrets in logs."""
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "<redacted>"
    return f"{token[:4]}...{token[-4:]}"


def _utc_now_iso() -> str:
    """UTC timestamp in ISO-8601 with milliseconds and Z suffix."""
    return (
        datetime.now(timezone.utc)  # noqa: UP017 - use timezone.utc for broad compatibility
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _tracer_context_metadata() -> dict[str, Any]:
    """
    Optional context from Tracer CLI / CI.
    Attach to each event to make joins & debugging easier.
    """
    ctx: dict[str, Any] = {}

    tracer_run_id = (os.getenv("TRACER_RUN_ID") or "").strip()
    tracer_trace_id = (os.getenv("TRACER_TRACE_ID") or "").strip()
    tracer_pipeline = (os.getenv("TRACER_PIPELINE_NAME") or "").strip()
    tracer_org = (os.getenv("TRACER_ORG_SLUG") or "").strip()

    if tracer_run_id:
        ctx["tracer_run_id"] = tracer_run_id
    if tracer_trace_id:
        ctx["tracer_trace_id"] = tracer_trace_id
    if tracer_pipeline:
        ctx["tracer_pipeline_name"] = tracer_pipeline
    if tracer_org:
        ctx["tracer_org_slug"] = tracer_org

    # Optional GitHub Actions context (helps when debugging CI)
    for k in (
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_WORKFLOW",
        "GITHUB_JOB",
        "GITHUB_REPOSITORY",
        "GITHUB_SHA",
    ):
        v = (os.getenv(k) or "").strip()
        if v:
            ctx[k.lower()] = v

    return ctx


def emit_tool_event(
    *,
    trace_id: str,
    run_id: str,
    run_name: str,
    tool_id: str,
    tool_name: str,
    tool_cmd: str = "synthetic",
    start_time: str | None = None,
    end_time: str | None = None,
    exit_code: int = 0,
    metadata: dict[str, Any] | None = None,
    timeout_s: float = 5.0,
) -> bool:
    """
    Best-effort event emitter to Tracer ingest endpoint.
    Returns True if sent successfully, False otherwise.

    Notes:
    - "metadata" is merged with optional Tracer/CI context so the backend can
      join/debug even if some ids are off during early integration.
    """
    base_url = os.getenv("TRACER_API_URL", "").strip().rstrip("/")
    token = os.getenv("TRACER_INGEST_TOKEN", "").strip()

    _log(
        "env snapshot: "
        f"TRACER_API_URL={base_url if base_url else '<missing>'} "
        f"TRACER_INGEST_TOKEN={_redact_token(token) if token else '<missing>'}"
    )

    if not base_url or not token:
        _log("skip: missing TRACER_API_URL and/or TRACER_INGEST_TOKEN")
        return False

    start = start_time or _utc_now_iso()
    end = end_time or start

    # Merge context metadata (context first, explicit metadata wins)
    merged_meta = {**_tracer_context_metadata(), **(metadata or {})}

    payload = {
        "events": [
            {
                "trace_id": trace_id,
                "run_id": run_id,
                "span_id": str(uuid.uuid4()),
                "run_name": run_name,
                "tool_id": tool_id,
                "tool_name": tool_name,
                "tool_cmd": tool_cmd,
                "start_time": start,
                "end_time": end,
                "exit_code": int(exit_code),
                "metadata": merged_meta,
            }
        ]
    }

    url = f"{base_url}/api/tools/ingest"
    _log(f"POST {url} tool_id={tool_id} exit_code={exit_code} timeout_s={timeout_s}")

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )

        ok = 200 <= resp.status_code < 300
        _log(f"response: status={resp.status_code} ok={ok}")

        if not ok:
            # Keep response small to avoid noise / accidental secrets
            body_preview = (resp.text or "")[:500]
            _log(f"response body (preview): {body_preview}")

        return ok

    except requests.RequestException as exc:
        _log(f"request error: {type(exc).__name__}: {exc}")
        return False
    except Exception as exc:
        _log(f"unexpected error: {type(exc).__name__}: {exc}")
        return False


class StepTimer:
    """Helper to measure step duration and emit a single event on completion."""

    def __init__(
        self,
        trace_id: str,
        run_id: str,
        run_name: str,
        tool_id: str,
        tool_name: str,
        tool_cmd: str,
    ):
        self.trace_id = trace_id
        self.run_id = run_id
        self.run_name = run_name
        self.tool_id = tool_id
        self.tool_name = tool_name
        self.tool_cmd = tool_cmd
        self.start_iso = _utc_now_iso()
        self.start_monotonic = time.monotonic()

        _log(f"StepTimer start: tool_id={tool_id} start={self.start_iso}")

    def finish(self, *, exit_code: int, metadata: dict[str, Any] | None = None) -> bool:
        end_iso = _utc_now_iso()
        runtime_ms = int((time.monotonic() - self.start_monotonic) * 1000)

        meta = dict(metadata or {})
        meta["runtime_ms"] = runtime_ms

        _log(
            f"StepTimer finish: tool_id={self.tool_id} "
            f"runtime_ms={runtime_ms} exit_code={exit_code}"
        )

        return emit_tool_event(
            trace_id=self.trace_id,
            run_id=self.run_id,
            run_name=self.run_name,
            tool_id=self.tool_id,
            tool_name=self.tool_name,
            tool_cmd=self.tool_cmd,
            start_time=self.start_iso,
            end_time=end_iso,
            exit_code=exit_code,
            metadata=meta,
        )
