"""Lightweight FastAPI server for remote investigations.

Wraps the sequential investigation runner so that an EC2 instance can
accept alert payloads over HTTP, run investigations, and persist results
as ``.md`` files for later retrieval.

Start with::

    uvicorn app.remote.server:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel

from app.version import get_version

load_dotenv(override=False)

INVESTIGATIONS_DIR = Path("/opt/opensre/investigations")
_AUTH_KEY = os.getenv("OPENSRE_API_KEY", "")


def _check_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests when OPENSRE_API_KEY is set and the header doesn't match."""
    if _AUTH_KEY and x_api_key != _AUTH_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)
    yield


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


class InvestigateResponse(BaseModel):
    id: str
    report: str
    root_cause: str
    problem_md: str


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
    return {"ok": True, "version": get_version()}


@app.post("/investigate", response_model=InvestigateResponse)
def investigate(req: InvestigateRequest) -> InvestigateResponse:
    """Run an investigation and persist the result as a ``.md`` file."""
    import logging
    import traceback

    from app.cli.investigate import run_investigation_cli

    logger = logging.getLogger(__name__)

    try:
        result = run_investigation_cli(
            raw_alert=req.raw_alert,
            alert_name=req.alert_name,
            pipeline_name=req.pipeline_name,
            severity=req.severity,
        )
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Investigation failed: %s\n%s", exc, tb)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    alert_name = req.alert_name or req.raw_alert.get("alert_name") or "incident"
    pipeline_name = req.pipeline_name or req.raw_alert.get("pipeline_name") or "unknown"
    severity = req.severity or req.raw_alert.get("severity") or "warning"

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
    )


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
    if not re.fullmatch(r"[\w\-]+", inv_id):
        raise HTTPException(status_code=400, detail="Invalid investigation ID")
    path = _safe_investigation_path(inv_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Investigation {inv_id} not found")
    return Response(content=path.read_text(encoding="utf-8"), media_type="text/markdown")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_investigation_path(inv_id: str) -> Path:
    """Resolve an investigation file path with path-traversal protection."""
    safe_base = str(INVESTIGATIONS_DIR.resolve()) + os.sep
    candidate = str((INVESTIGATIONS_DIR / f"{inv_id}.md").resolve())
    if not candidate.startswith(safe_base):
        raise ValueError(f"Invalid investigation ID: {inv_id}")
    return Path(candidate)


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
    md = (
        f"# Investigation: {alert_name}\n"
        f"Pipeline: {pipeline_name} | Severity: {severity}\n"
        f"Date: {ts}\n\n"
        f"## Root Cause\n{result.get('root_cause', 'N/A')}\n\n"
        f"## Report\n{result.get('report', 'N/A')}\n\n"
        f"## Problem Description\n{result.get('problem_md', 'N/A')}\n"
    )
    path = _safe_investigation_path(inv_id)
    path.write_text(md, encoding="utf-8")
    return path
