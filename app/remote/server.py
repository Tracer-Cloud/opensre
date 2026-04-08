"""Lightweight FastAPI server for remote investigations.

Wraps the sequential investigation runner so that an EC2 instance can
accept alert payloads over HTTP, run investigations, and persist results
as ``.md`` files for later retrieval.

Start with::

    uvicorn app.remote.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from app.remote.system_metrics import collect_system_metrics
from app.remote.vercel_poller import (
    VercelInvestigationCandidate,
    VercelPoller,
    VercelResolutionError,
    enrich_remote_alert_from_vercel,
)
from app.version import get_version

load_dotenv(override=False)

INVESTIGATIONS_DIR = Path("/opt/opensre/investigations")
_AUTH_KEY = os.getenv("OPENSRE_API_KEY", "")
logger = logging.getLogger(__name__)


def _check_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests when OPENSRE_API_KEY is set and the header doesn't match."""
    if _AUTH_KEY and x_api_key != _AUTH_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)
    poller = VercelPoller(investigations_dir=INVESTIGATIONS_DIR)
    poller_task: asyncio.Task[None] | None = None
    if poller.is_enabled:
        poller_task = asyncio.create_task(poller.run_forever(_handle_polled_candidate))
    try:
        yield
    finally:
        if poller_task is not None:
            poller_task.cancel()
            with suppress(asyncio.CancelledError):
                await poller_task


app = FastAPI(
    title="OpenSRE Remote",
    version=get_version(),
    lifespan=_lifespan,
    dependencies=[Depends(_check_api_key)],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class InvestigateRequest(BaseModel):
    raw_alert: dict[str, Any]
    alert_name: str | None = None
    pipeline_name: str | None = None
    severity: str | None = None
    vercel_url: str | None = None


class InvestigateResponse(BaseModel):
    id: str
    report: str
    root_cause: str
    problem_md: str
    is_noise: bool = False


class InvestigationMeta(BaseModel):
    id: str
    filename: str
    created_at: str
    alert_name: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/ok")
def health_check() -> dict[str, Any]:
    return {
        "ok": True,
        "version": get_version(),
        "server_type": "lightweight",
        "endpoints": ["/investigate", "/investigate/stream", "/investigations"],
        "system": collect_system_metrics(),
    }


@app.post("/investigate", response_model=InvestigateResponse)
def investigate(req: InvestigateRequest) -> InvestigateResponse:
    """Run an investigation and persist the result as a ``.md`` file."""
    try:
        raw_alert = _normalized_request_alert(req)
        result, alert_name, pipeline_name, severity = _execute_investigation(
            raw_alert=raw_alert,
            alert_name=req.alert_name,
            pipeline_name=req.pipeline_name,
            severity=req.severity,
        )
    except VercelResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Investigation failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    inv_id = _make_id(alert_name)
    _save_investigation(
        inv_id=inv_id,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        result=result,
    )

    return InvestigateResponse(
        id=inv_id,
        report=result.get("report", ""),
        root_cause=result.get("root_cause", ""),
        problem_md=result.get("problem_md", ""),
        is_noise=bool(result.get("is_noise")),
    )


@app.post("/investigate/stream")
async def investigate_stream(req: InvestigateRequest) -> Response:
    """Stream investigation events as SSE using ``astream_events``.

    Returns ``text/event-stream`` with the same SSE format the LangGraph
    API uses, so ``RemoteAgentClient`` / ``StreamRenderer`` can consume
    this endpoint identically to a LangGraph deployment.

    The final pipeline state is accumulated during streaming and persisted
    as a ``.md`` file once the stream completes, matching the behaviour of
    the blocking ``/investigate`` endpoint.
    """
    from app.cli.investigate import resolve_investigation_context
    from app.config import LLMSettings
    from app.pipeline.runners import astream_investigation

    LLMSettings.from_env()
    try:
        raw_alert = _normalized_request_alert(req)
    except VercelResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    alert_name, pipeline_name, severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=req.alert_name,
        pipeline_name=req.pipeline_name,
        severity=req.severity,
    )

    accumulated_state: dict[str, Any] = {}

    async def _event_generator() -> AsyncIterator[str]:
        try:
            async for event in astream_investigation(
                alert_name,
                pipeline_name,
                severity,
                raw_alert=raw_alert,
            ):
                if event.kind == "on_chain_end":
                    output = event.data.get("data", {}).get("output", {})
                    if isinstance(output, dict):
                        accumulated_state.update(output)

                payload = _json.dumps(event.data, default=str)
                yield f"event: {event.event_type}\ndata: {payload}\n\n"
            yield "event: end\ndata: {}\n\n"
        except Exception:
            logger.exception("Streaming investigation failed")
            yield 'event: error\ndata: {"detail": "internal error"}\n\n'
        finally:
            _persist_streamed_result(
                alert_name=alert_name,
                pipeline_name=pipeline_name,
                severity=severity,
                state=accumulated_state,
                logger=logger,
            )

    return StreamingResponse(  # type: ignore[return-value]
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _persist_streamed_result(
    *,
    alert_name: str,
    pipeline_name: str,
    severity: str,
    state: dict[str, Any],
    logger: Any,
) -> None:
    """Save a ``.md`` investigation file from the accumulated stream state."""
    if not state.get("root_cause") and not state.get("report"):
        logger.info("Streamed investigation produced no report; skipping persist.")
        return
    try:
        inv_id = _make_id(alert_name)
        _save_investigation(
            inv_id=inv_id,
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            result=state,
        )
        logger.info("Persisted streamed investigation: %s", inv_id)
    except Exception:
        logger.exception("Failed to persist streamed investigation")


async def _handle_polled_candidate(candidate: VercelInvestigationCandidate) -> bool:
    """Run and persist RCA for a polled Vercel candidate."""
    try:
        result, alert_name, pipeline_name, severity = await asyncio.to_thread(
            _execute_investigation,
            raw_alert=candidate.raw_alert,
            alert_name=candidate.alert_name,
            pipeline_name=candidate.pipeline_name,
            severity=candidate.severity,
        )
    except Exception:
        logger.exception(
            "Background Vercel investigation failed for deployment %s",
            candidate.dedupe_key,
        )
        return False

    inv_id = _make_id(alert_name)
    await asyncio.to_thread(
        _save_investigation,
        inv_id=inv_id,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        result=result,
    )
    logger.info(
        "Persisted background Vercel investigation %s for deployment %s",
        inv_id,
        candidate.dedupe_key,
    )
    return True


@app.get("/investigations", response_model=list[InvestigationMeta])
def list_investigations() -> list[InvestigationMeta]:
    """List all persisted investigation ``.md`` files."""
    items: list[InvestigationMeta] = []
    for path in sorted(INVESTIGATIONS_DIR.glob("*.md"), reverse=True):
        inv_id = path.stem
        parts = inv_id.split("_", maxsplit=2)
        alert = parts[2] if len(parts) > 2 else inv_id
        created = _id_to_iso(inv_id)
        items.append(
            InvestigationMeta(
                id=inv_id,
                filename=path.name,
                created_at=created,
                alert_name=alert.replace("-", " "),
            )
        )
    return items


@app.get("/investigations/{inv_id}")
def get_investigation(inv_id: str) -> Response:
    """Return the raw ``.md`` content of a single investigation."""
    path = _safe_investigation_path(inv_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Investigation {inv_id} not found")
    return Response(content=path.read_text(encoding="utf-8"), media_type="text/markdown")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SAFE_INV_ID = re.compile(r"[\w\-]+")


def _safe_investigation_path(inv_id: str) -> Path:
    """Resolve an investigation file path with path-traversal protection.

    Rejects any ID that contains characters outside ``[\\w-]`` and verifies
    the normalised path stays inside INVESTIGATIONS_DIR.
    """
    if not _SAFE_INV_ID.fullmatch(inv_id):
        raise HTTPException(status_code=400, detail="Invalid investigation ID")
    base = os.path.realpath(INVESTIGATIONS_DIR)
    fullpath = os.path.realpath(os.path.join(base, f"{inv_id}.md"))
    if not fullpath.startswith(base + os.sep):
        raise HTTPException(status_code=400, detail="Invalid investigation ID")
    return Path(fullpath)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _make_id(alert_name: str) -> str:
    ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{_slugify(alert_name)}"


def _id_to_iso(inv_id: str) -> str:
    """Best-effort parse of ``YYYYMMDD_HHMMSS_slug`` into ISO 8601."""
    try:
        date_part = inv_id[:15]  # YYYYMMDD_HHMMSS
        dt = datetime.strptime(date_part, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
        return dt.isoformat()
    except (ValueError, IndexError):
        return ""


def _save_investigation(
    *,
    inv_id: str,
    alert_name: str,
    pipeline_name: str,
    severity: str,
    result: dict[str, Any],
) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    if result.get("is_noise"):
        root_cause = "Alert classified as noise - no investigation performed."
        report = "The alert was automatically classified as noise (non-actionable) during extraction."
        problem_md = result.get("problem_md") or "N/A"
    else:
        root_cause = result.get("root_cause") or "N/A"
        report = result.get("report") or "N/A"
        problem_md = result.get("problem_md") or "N/A"

    md = (
        f"# Investigation: {alert_name}\n"
        f"Pipeline: {pipeline_name} | Severity: {severity}\n"
        f"Date: {ts}\n\n"
        f"## Root Cause\n{root_cause}\n\n"
        f"## Report\n{report}\n\n"
        f"## Problem Description\n{problem_md}\n"
    )
    path = _safe_investigation_path(inv_id)
    path.write_text(md, encoding="utf-8")
    return path


def _normalized_request_alert(req: InvestigateRequest) -> dict[str, Any]:
    """Merge optional Vercel URL input into the alert and resolve it when present."""
    raw_alert = dict(req.raw_alert)
    if req.vercel_url:
        raw_alert.setdefault("vercel_url", req.vercel_url)
        raw_alert.setdefault("vercel_log_url", req.vercel_url)
    resolved_alert = enrich_remote_alert_from_vercel(raw_alert)
    return resolved_alert if isinstance(resolved_alert, dict) else raw_alert


def _execute_investigation(
    *,
    raw_alert: dict[str, Any],
    alert_name: str | None,
    pipeline_name: str | None,
    severity: str | None,
) -> tuple[dict[str, Any], str, str, str]:
    """Run the RCA pipeline and return both the result and resolved metadata."""
    from app.cli.investigate import resolve_investigation_context, run_investigation_cli

    resolved_alert_name, resolved_pipeline_name, resolved_severity = resolve_investigation_context(
        raw_alert=raw_alert,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )
    result = run_investigation_cli(
        raw_alert=raw_alert,
        alert_name=resolved_alert_name,
        pipeline_name=resolved_pipeline_name,
        severity=resolved_severity,
    )
    return result, resolved_alert_name, resolved_pipeline_name, resolved_severity
