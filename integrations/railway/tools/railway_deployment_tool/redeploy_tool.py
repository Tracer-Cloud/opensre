from __future__ import annotations

from typing import Any, ClassVar

from core.domain.types.evidence import EvidenceSource
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from integrations.config_models import RailwayIntegrationConfig
from integrations.railway.client import (
    RailwayClient,
    RailwayOperationError,
    railway_config_from_sources,
)


class RedeployRailwayServiceTool(BaseTool):
    name = "redeploy_railway_service"
    source: ClassVar[EvidenceSource] = "railway"
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    side_effect_level = SideEffectLevel.EXTERNAL
    requires_approval = True
    approval_reason = "Triggers a Railway redeploy of the selected service."
    description = "Request a Railway service redeploy after explicit confirmation."
    use_cases = [
        "User explicitly asks to redeploy a Railway service and confirms it",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Railway project ID or name."},
            "service": {"type": "string", "description": "Railway service ID or name."},
            "environment": {"type": "string", "description": "Railway environment name or ID."},
            "confirm": {"type": "boolean", "description": "Must be true to request the redeploy."},
        },
        "additionalProperties": False,
    }
    outputs = {"redeploy": "Requested Railway deployment ID.", "error": "Failure detail."}

    def is_available(self, sources: dict[str, dict[object, object]]) -> bool:
        return RailwayClient(railway_config_from_sources(sources)).is_available

    def extract_params(self, sources: dict[str, dict[object, object]]) -> dict[str, object]:
        return {"railway_config": railway_config_from_sources(sources)}

    def run(
        self,
        project: str = "",
        service: str = "",
        environment: str = "",
        confirm: bool = False,
        railway_config: RailwayIntegrationConfig | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if confirm is not True:
            return {
                "status": "failed",
                "error": "Confirmation required: set confirm=true to request the redeploy.",
                "error_type": "confirmation_required",
            }
        client = RailwayClient(railway_config or RailwayIntegrationConfig())
        try:
            scope = client.resolve_scope_object(project, service, environment)
            result = client.redeploy(scope)
        except RailwayOperationError as exc:
            return {"status": "failed", "error": str(exc), "error_type": exc.error_type}
        return {
            "status": "ok",
            "redeploy": {
                "project": scope.project,
                "service": scope.service,
                "environment": scope.environment,
                "deployment_id": result.deployment_id,
                "requested": True,
            },
        }


redeploy_railway_service = RedeployRailwayServiceTool()
tool(
    redeploy_railway_service,
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
)
