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
import secrets
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from pydantic import BaseModel
from starlette.responses import JSONResponse, StreamingResponse

from app.remote.vercel_poller import (
    VercelInvestigationCandidate,
    VercelPoller,
    VercelResolutionError,
    enrich_remote_alert_from_vercel,
)
from app.version import get_version

load_dotenv(override=False)

INVESTIGATIONS_DIR = Path(os.getenv("INVESTIGATIONS_DIR", "/opt/opensre/investigations"))
_AUTH_KEY = os.getenv("OPENSRE_API_KEY")
_AUTH_EXEMPT_PATHS = {
    "/discord/interactions",
    "/health/deep",
    "/ok",
    "/version",
}
_STARTED_AT = datetime.now(tz=UTC)
_START_TIME_MONOTONIC = time.monotonic()
_INSTANCE_METADATA: dict[str, str | None] = {
    "instance_id": None,
    "region": os.getenv("AWS_REGION") or None,
    "public_ip": None,
}
logger = logging.getLogger(__name__)


def _configured_auth_key() -> str | None:
    auth_key = (_AUTH_KEY or "").strip()
    return auth_key or None


def _check_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Reject protected remote API requests unless a valid API key is configured."""
    if request.url.path in _AUTH_EXEMPT_PATHS:
        return

    auth_key = _configured_auth_key()
    if auth_key is None or not secrets.compare_digest(x_api_key or "", auth_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)
    _refresh_instance_metadata()

    poller_task: asyncio.Task[None] | None = None
    poller = VercelPoller(investigations_dir=INVESTIGATIONS_DIR)
    if poller.is_enabled:
        poller_task = asyncio.create_task(
            poller.run_forever(_handle_polled_candidate),
            name="vercel-poller",
        )

    try:
        yield
    finally:
        if poller_task is not None:
            poller_task.cancel()
            with suppress(asyncio.CancelledError):
                await poller_task  # noqa: B018  -- intentional await for clean shutdown


app = FastAPI(
    title="OpenSRE Remote",
    version=get_version(),
    lifespan=_lifespan,
    dependencies=[Depends(_check_api_key)],
)

# Separate router to bypass global _check_api_key, as Discord controls the request format
discord_router = APIRouter()


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


class DiscordInteraction(BaseModel):
    type: int
    data: dict[str, Any] | None = None
    token: str | None = None
    application_id: str | None = None
    channel_id: str | None = None


class DeepHealthCheck(BaseModel):
    name: str
    status: str
    detail: str


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

_DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "")
_DISCORD_APPLICATION_ID = os.getenv("DISCORD_APPLICATION_ID", "")
_DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")


def _verify_discord_signature(body: bytes, signature: str, timestamp: str) -> None:
    if not _DISCORD_PUBLIC_KEY:
        raise HTTPException(status_code=500, detail="DISCORD_PUBLIC_KEY not configured")
    try:
        VerifyKey(bytes.fromhex(_DISCORD_PUBLIC_KEY)).verify(
            timestamp.encode() + body, bytes.fromhex(signature)
        )
    except (BadSignatureError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid request signature") from exc


def _discord_post_followup(
    application_id: str,
    interaction_token: str,
    *,
    content: str = "",
    embeds: list[dict[str, Any]] | None = None,
) -> None:
    """Complete a deferred Discord interaction by posting a followup message."""
    import httpx

    payload: dict[str, Any] = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    try:
        headers: dict[str, str] = {}
        if _DISCORD_BOT_TOKEN:
            headers["Authorization"] = f"Bot {_DISCORD_BOT_TOKEN}"
        resp = httpx.post(
            f"https://discord.com/api/v10/webhooks/{application_id}/{interaction_token}",
            json=payload,
            headers=headers or None,
            timeout=15.0,
        )
        if resp.status_code not in (200, 204):
            logger.warning("[discord] followup failed: %s %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("[discord] followup request failed")


async def _run_discord_investigation(interaction: DiscordInteraction) -> None:
    """Background task: run investigation from a Discord slash command and post results."""
    # Extract the alert value from slash command options
    options = (interaction.data or {}).get("options", [])
    alert_raw = next(
        (str(opt.get("value", "")) for opt in options if opt.get("name") == "alert"), ""
    )

    # Accept JSON alert payload or plain-text description
    try:
        raw_alert: dict[str, Any] = _json.loads(alert_raw)
    except (_json.JSONDecodeError, ValueError):
        raw_alert = {"alert_name": alert_raw, "description": alert_raw}

    try:
        result, resolved_name, _pipeline, _sev = await asyncio.to_thread(
            _execute_investigation,
            raw_alert=raw_alert,
            alert_name=raw_alert.get("alert_name"),
            pipeline_name=raw_alert.get("pipeline_name"),
            severity=raw_alert.get("severity"),
        )
    except Exception:
        logger.exception("[discord] background investigation failed")
        app_id = interaction.application_id or _DISCORD_APPLICATION_ID
        if app_id and interaction.token:
            _discord_post_followup(
                app_id,
                interaction.token,
                content="Investigation failed — check server logs for details.",
            )
        return

    root_cause = result.get("root_cause") or "N/A"
    report = result.get("report") or "N/A"
    is_noise = bool(result.get("is_noise"))

    def _truncate(text: str, limit: int = 1024) -> str:
        return (text[: limit - 1] + "…") if len(text) > limit else text

    raw_title = f"Investigation Complete: {resolved_name}"
    embed: dict[str, Any] = {
        "title": _truncate(raw_title, 256),
        "color": 0x95A5A6 if is_noise else 0xE74C3C,
        "fields": [
            {"name": "Root Cause", "value": _truncate(root_cause), "inline": False},
            {"name": "Report", "value": _truncate(report), "inline": False},
        ],
        "footer": {"text": "OpenSRE Investigation"},
    }

    # Post via interaction followup webhook (the deferred response requires this)
    app_id = interaction.application_id or _DISCORD_APPLICATION_ID
    if app_id and interaction.token:
        await asyncio.to_thread(_discord_post_followup, app_id, interaction.token, embeds=[embed])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@discord_router.post("/discord/interactions")
async def discord_interactions(request: Request, background_tasks: BackgroundTasks) -> Response:
    body = await request.body()
    sig = request.headers.get("X-Signature-Ed25519", "")
    ts = request.headers.get("X-Signature-Timestamp", "")
    _verify_discord_signature(body, sig, ts)

    interaction = DiscordInteraction.model_validate_json(body)

    if interaction.type == 1:  # PING — Discord endpoint verification
        return JSONResponse({"type": 1})

    if interaction.type == 2:  # APPLICATION_COMMAND — slash command
        background_tasks.add_task(_run_discord_investigation, interaction)
        # type 5 = DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE ("Bot is thinking…")
        return JSONResponse({"type": 5})

    raise HTTPException(status_code=400, detail="Unsupported interaction type")


app.include_router(discord_router)


@app.get("/ok")
def health_check() -> dict[str, Any]:
    uptime_seconds = max(0, int(time.monotonic() - _START_TIME_MONOTONIC))
    return {
        "ok": True,
        "version": get_version(),
        "started_at": _STARTED_AT.isoformat(),
        "uptime_seconds": uptime_seconds,
        "instance_id": _INSTANCE_METADATA.get("instance_id"),
        "region": _INSTANCE_METADATA.get("region"),
        "public_ip": _INSTANCE_METADATA.get("public_ip"),
    }


@app.get("/version")
def version_check() -> dict[str, str]:
    return {"version": get_version()}


@app.get("/health/deep")
def deep_health_check() -> dict[str, Any]:
    checks = [_check_llm_connectivity(), _check_disk_health(), _check_memory_health()]
    status = "passed"
    if any(check.status == "failed" for check in checks):
        status = "failed"
    elif any(check.status in {"warn", "missing"} for check in checks):
        status = "warn"

    return {
        "status": status,
        "checks": [check.model_dump() for check in checks],
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


def _refresh_instance_metadata() -> None:
    token = _imds_token()
    _INSTANCE_METADATA["instance_id"] = _imds_get("latest/meta-data/instance-id", token=token)
    if not _INSTANCE_METADATA.get("region"):
        _INSTANCE_METADATA["region"] = _imds_get("latest/meta-data/placement/region", token=token)
    _INSTANCE_METADATA["public_ip"] = _imds_get("latest/meta-data/public-ipv4", token=token)


def _imds_token() -> str | None:
    req = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
    )
    try:
        with urllib.request.urlopen(req, timeout=0.3) as response:
            return response.read().decode("utf-8").strip() or None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _imds_get(path: str, *, token: str | None) -> str | None:
    headers = {"X-aws-ec2-metadata-token": token} if token else {}
    req = urllib.request.Request(f"http://169.254.169.254/{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=0.3) as response:
            return response.read().decode("utf-8").strip() or None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _check_llm_connectivity() -> DeepHealthCheck:
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider != "bedrock":
        return DeepHealthCheck(
            name="LLM provider",
            status="passed",
            detail="Bedrock check skipped (LLM_PROVIDER is not bedrock).",
        )

    region = _INSTANCE_METADATA.get("region") or os.getenv("AWS_REGION") or "us-east-1"
    try:
        import boto3

        bedrock = boto3.client("bedrock", region_name=region)
        bedrock.list_foundation_models(byProvider="Anthropic")
        return DeepHealthCheck(
            name="Bedrock connectivity",
            status="passed",
            detail=f"Connected to Bedrock in {region}.",
        )
    except Exception as exc:  # noqa: BLE001
        return DeepHealthCheck(
            name="Bedrock connectivity",
            status="failed",
            detail=f"Failed to reach Bedrock in {region}: {type(exc).__name__}: {exc}",
        )


def _check_disk_health() -> DeepHealthCheck:
    usage = shutil.disk_usage("/")
    if usage.total == 0:
        return DeepHealthCheck(
            name="Disk", status="missing", detail="Unable to determine disk size."
        )
    used_pct = int((usage.used / usage.total) * 100)
    status = "passed" if used_pct < 85 else "warn"
    detail = f"{used_pct}% used ({usage.used // (1024**3)}GiB / {usage.total // (1024**3)}GiB)"
    return DeepHealthCheck(name="Disk", status=status, detail=detail)


def _check_memory_health() -> DeepHealthCheck:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return DeepHealthCheck(
            name="Memory",
            status="missing",
            detail="/proc/meminfo unavailable on this platform.",
        )

    values: dict[str, int] = {}
    try:
        for line in meminfo_path.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            number = raw.strip().split(" ", 1)[0]
            if number.isdigit():
                values[key] = int(number)
    except OSError as exc:
        return DeepHealthCheck(
            name="Memory", status="missing", detail=f"Unable to read meminfo: {exc}"
        )

    total_kb = values.get("MemTotal")
    avail_kb = values.get("MemAvailable")
    if not total_kb or avail_kb is None:
        return DeepHealthCheck(
            name="Memory", status="missing", detail="Incomplete /proc/meminfo data."
        )

    used_pct = int(((total_kb - avail_kb) / total_kb) * 100)
    status = "passed" if used_pct < 90 else "warn"
    detail = f"{used_pct}% used ({(total_kb - avail_kb) // 1024}MiB / {total_kb // 1024}MiB)"
    return DeepHealthCheck(name="Memory", status=status, detail=detail)


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
        root_cause = "Alert classified as noise — no investigation performed."
        report = (
            "The alert was automatically classified as noise (non-actionable) during extraction."
        )
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
