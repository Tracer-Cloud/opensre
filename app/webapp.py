from __future__ import annotations

import importlib

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
    try:
        importlib.import_module("app.graph_pipeline")
    except Exception:
        return False
    return True


def _llm_configured() -> bool:
    try:
        LLMSettings.from_env()
    except Exception:
        return False
    return True


def get_health_response() -> HealthResponse:
    is_graph_loaded = _graph_loaded()
    is_llm_configured = _llm_configured()
    env = get_environment().value

    return HealthResponse(
        ok=is_graph_loaded and is_llm_configured,
        version=get_version(),
        graph_loaded=is_graph_loaded,
        llm_configured=is_llm_configured,
        env=env,
    )

@app.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    health_response = get_health_response()
    response.status_code = status.HTTP_200_OK if health_response.ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return health_response
