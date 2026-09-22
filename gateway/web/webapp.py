"""The gateway's single FastAPI app: health probes and alert intake.

Every HTTP endpoint OpenSRE serves lives here, on one port — ``/`` ``/health``
``/ok`` (health probes), ``/healthz`` (liveness), and ``POST /alerts`` (external
alert pushes into the process-wide :class:`AlertInbox`). Hosted by the
gateway daemon and the interactive shell via :mod:`gateway.web.web_server`, or
standalone via ``uvicorn gateway.web.webapp:app``.
"""

from __future__ import annotations

from http import HTTPStatus

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from bootstrap.process import WEB_PROFILE, configure_process
from config.environment import get_environment
from config.llm_settings import LLMSettings
from config.version import get_opensre_version
from gateway.core.process.component_status import read_component_status
from gateway.core.process.readiness import is_gateway_ready
from gateway.core.process.shutdown_record import PREVIOUS_SHUTDOWN_COMPONENT
from gateway.core.process.state_counts import read_state_counts
from infrastructure.alert_intake import router as alert_router
from infrastructure.request_body_limit import RequestBodyLimitMiddleware

configure_process(WEB_PROFILE)  # env → sentry → adapters

__all__ = ["app"]


class HealthResponse(BaseModel):
    ok: bool
    version: str
    llm_configured: bool
    env: str
    #: State on the volume, so a start can be compared with the stop before it.
    sessions: int
    memory_notes: int
    scheduled_tasks: int
    #: How the previous gateway process ended; empty outside a gateway process.
    previous_shutdown: str


app = FastAPI()
# Above routing: every mutating route is bounded before FastAPI buffers a body.
app.add_middleware(RequestBodyLimitMiddleware)
# Health liveness (/healthz) and alert intake (/alerts) live in the shared
# router so the interactive shell can serve them without importing the gateway.
app.include_router(alert_router)


def get_health_response() -> HealthResponse:
    try:
        LLMSettings.from_env()
        llm_configured = True
    except ValidationError:
        llm_configured = False

    counts = read_state_counts()
    components = read_component_status()
    previous_shutdown = components.get(PREVIOUS_SHUTDOWN_COMPONENT, "")
    return HealthResponse(
        ok=llm_configured,
        version=get_opensre_version(),
        llm_configured=llm_configured,
        env=get_environment().value,
        sessions=counts.sessions,
        memory_notes=counts.memory_notes,
        scheduled_tasks=counts.scheduled_tasks,
        previous_shutdown=previous_shutdown,
    )


@app.get("/", response_model=HealthResponse)
@app.get("/health", response_model=HealthResponse)
@app.get("/ok", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    health_response = get_health_response()
    response.status_code = HTTPStatus.OK if health_response.ok else HTTPStatus.SERVICE_UNAVAILABLE
    return health_response


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Report mandatory startup readiness separately from process liveness."""
    if is_gateway_ready():
        return JSONResponse({"status": "ready"}, status_code=HTTPStatus.OK)
    return JSONResponse({"status": "not_ready"}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)
