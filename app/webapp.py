from __future__ import annotations

import sys

from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from app.config import LLMSettings, get_environment
from app.version import get_version


class HealthResponse(BaseModel):
    ok: bool
    version: str
    graph_loaded: bool
    llm_configured: bool
    env: str


app = FastAPI()


def _graph_loaded() -> bool:
    return "app.graph_pipeline" in sys.modules


def _llm_configured() -> bool:
    try:
        LLMSettings.from_env()
    except Exception:
        return False
    return True


def get_health_response() -> HealthResponse:
    graph_loaded = _graph_loaded()
    llm_configured = _llm_configured()

    return HealthResponse(
        ok=graph_loaded and llm_configured,
        version=get_version(),
        graph_loaded=graph_loaded,
        llm_configured=llm_configured,
        env=get_environment().value,
    )


@app.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    health_response = get_health_response()
    response.status_code = status.HTTP_200_OK if health_response.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return health_response
