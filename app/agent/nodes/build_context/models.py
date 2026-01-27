"""Models for frame_problem context."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextSourceError(BaseModel):
    """Structured error from a context source."""

    source: str = Field(description="Context source name")
    message: str = Field(description="Error message for the source failure")


class TracerWebRunContext(BaseModel):
    """Context gathered from Tracer Web App."""

    model_config = ConfigDict(extra="allow")

    found: bool
    error: str | None = None
    pipeline_name: str | None = None
    run_id: str | None = None
    run_name: str | None = None
    trace_id: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    run_cost: float | None = None
    tool_count: int | None = None
    user_email: str | None = None
    instance_type: str | None = None
    region: str | None = None
    log_file_count: int | None = None
    run_url: str | None = None
    pipelines_checked: int | None = None


class ContextEvidence(BaseModel):
    """Typed wrapper for evidence gathered before runtime investigation."""

    model_config = ConfigDict(extra="allow")

    tracer_web_run: TracerWebRunContext | None = None
    context_errors: list[ContextSourceError] = Field(default_factory=list)

    def to_state(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude_defaults=True)
