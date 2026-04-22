from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ValidationError

from app.cli.investigate import run_investigation_cli


class RunRCAInput(BaseModel):
    alert_payload: dict[str, Any] = Field(..., description="Raw alert payload")
    alert_name: str | None = Field(default=None, description="Optional alert name override")
    pipeline_name: str | None = Field(default=None, description="Optional pipeline name override")
    severity: str | None = Field(default=None, description="Optional severity override")


class RunRCAOutput(BaseModel):
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None


mcp = FastMCP("opensre")


def _apply_overrides(payload: dict[str, Any], data: RunRCAInput) -> dict[str, Any]:
    updated = dict(payload)

    common_labels = dict(updated.get("commonLabels", {}))
    common_annotations = dict(updated.get("commonAnnotations", {}))

    if data.alert_name:
        common_labels["alertname"] = data.alert_name
    if data.pipeline_name:
        common_labels["pipeline_name"] = data.pipeline_name
    if data.severity:
        common_labels["severity"] = data.severity

    updated["commonLabels"] = common_labels
    updated["commonAnnotations"] = common_annotations
    return updated


def _run_cli(
    payload: dict[str, Any],
    *,
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    return run_investigation_cli(
        raw_alert=payload,
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
    )


@mcp.tool(name="run_rca")
def run_rca(
    alert_payload: dict[str, Any],
    alert_name: str | None = None,
    pipeline_name: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Run the existing OpenSRE investigation workflow over MCP."""
    try:
        data = RunRCAInput(
            alert_payload=alert_payload,
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
        )

        payload = _apply_overrides(data.alert_payload, data)

        result = _run_cli(
            payload,
            alert_name=data.alert_name,
            pipeline_name=data.pipeline_name,
            severity=data.severity,
        )

        return RunRCAOutput(ok=True, result=result).model_dump()
    except ValidationError as err:
        return RunRCAOutput(ok=False, error=str(err), error_type=type(err).__name__).model_dump()
    except Exception as err:  # noqa: BLE001
        return RunRCAOutput(ok=False, error=str(err), error_type=type(err).__name__).model_dump()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
