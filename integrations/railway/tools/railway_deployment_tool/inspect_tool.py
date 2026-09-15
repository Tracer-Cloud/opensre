from __future__ import annotations

from typing import Any, ClassVar

from core.domain.types.evidence import EvidenceSource, record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework import tool
from infrastructure.text.truncation import truncate
from integrations.config_models import RailwayIntegrationConfig
from integrations.railway.client import (
    RailwayClient,
    RailwayOperationError,
    railway_config_from_sources,
)

#: git commit messages are free-form and unbounded; cap the length used in a
#: report summary so one long or multi-line message can't produce a
#: malformed or oversized report line.
_COMMIT_MESSAGE_TRUNCATE_LEN = 100
_COMMIT_HASH_DISPLAY_LEN = 12


def _map_inspect_railway_deployment(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the deployment's status and source commit."""
    if output.get("status") != "ok":
        return
    deployment = output.get("deployment") or {}
    if not deployment:
        return
    parts = [f"status {deployment.get('status') or 'unknown'}"]
    commit_hash = deployment.get("commit_hash")
    if commit_hash:
        parts.append(f"commit {str(commit_hash)[:_COMMIT_HASH_DISPLAY_LEN]}")
    commit_message = deployment.get("commit_message")
    if commit_message:
        safe_message = truncate(
            str(commit_message).replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
            _COMMIT_MESSAGE_TRUNCATE_LEN,
        )
        parts.append(f"'{safe_message}'")
    record_evidence_entry(
        evidence,
        source="inspect_railway_deployment",
        label="Railway Deployment",
        summary=", ".join(parts),
    )


class InspectRailwayDeploymentTool(BaseTool):
    name = "inspect_railway_deployment"
    source: ClassVar[EvidenceSource] = "railway"
    surfaces = (ToolSurface.CHAT, ToolSurface.ACTION)
    side_effect_level = SideEffectLevel.READ_ONLY
    description = (
        "Show the latest successful Railway deployment and source commit metadata for a service."
    )
    use_cases = [
        "Check which commit a Railway service is running after a deploy",
        "Confirm the latest successful Railway deployment before deciding to redeploy",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Railway project ID or name."},
            "service": {"type": "string", "description": "Railway service ID or name."},
            "environment": {"type": "string", "description": "Railway environment name or ID."},
        },
        "additionalProperties": False,
    }
    outputs = {
        "deployment": "Latest successful deployment and source metadata.",
        "error": "Failure detail.",
    }

    def is_available(self, sources: dict[str, dict[object, object]]) -> bool:
        return RailwayClient(railway_config_from_sources(sources)).is_available

    def extract_params(self, sources: dict[str, dict[object, object]]) -> dict[str, object]:
        return {"railway_config": railway_config_from_sources(sources)}

    def run(
        self,
        project: str = "",
        service: str = "",
        environment: str = "",
        railway_config: RailwayIntegrationConfig | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = RailwayClient(railway_config or RailwayIntegrationConfig())
        try:
            scope = client.resolve_scope_object(project, service, environment)
            deployment = client.inspect_deployment(scope)
        except RailwayOperationError as exc:
            return {"status": "failed", "error": str(exc), "error_type": exc.error_type}
        return {
            "status": "ok",
            "deployment": {
                "project": scope.project,
                "service": scope.service,
                "environment": scope.environment,
                "deployment_id": deployment.deployment_id,
                "status": deployment.status,
                "commit_hash": deployment.commit_hash,
                "commit_message": deployment.commit_message,
            },
        }


inspect_railway_deployment = InspectRailwayDeploymentTool()
tool(
    inspect_railway_deployment,
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    evidence_mapper=_map_inspect_railway_deployment,
)
