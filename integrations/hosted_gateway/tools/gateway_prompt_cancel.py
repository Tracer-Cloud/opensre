"""Tool: cancel one of the signed-in user's prompts on the organization's hosted gateway."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.hosted_gateway.client import HostedGatewayClient, HostedGatewayError
from integrations.hosted_gateway.tools.results import (
    SOURCE,
    failure_output,
    hosted_gateway_available,
)

TOOL_NAME = "cancel_hosted_gateway_prompt"
_COMPONENT = "integrations.hosted_gateway.tools.gateway_prompt_cancel.cancel_hosted_gateway_prompt"
_CANCELLED = "Cancelled prompt {prompt_id} before the hosted gateway started it."
_STOPPING = (
    "Asked the hosted gateway to stop prompt {prompt_id}; it stops at its next step. "
    "Ask about that prompt id later to confirm it reads as cancelled."
)


@tool(
    name=TOOL_NAME,
    source=SOURCE,
    display_name="Cancel hosted gateway prompt",
    description=(
        "Cancel one of the signed-in user's prompts on the OpenSRE hosted gateway by its "
        "prompt id (from ask_hosted_gateway). A queued prompt never runs; a running one "
        "stops at its next step. Use it only when the user asks to stop that work."
    ),
    use_cases=["Stop a hosted gateway prompt the user no longer wants, by its prompt id"],
    anti_examples=["Stopping the hosted gateway itself (use stop_hosted_gateway)"],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    is_available=hosted_gateway_available,
    input_schema={
        "type": "object",
        "properties": {
            "prompt_id": {
                "type": "string",
                "description": "The prompt id ask_hosted_gateway returned.",
            },
        },
        "required": ["prompt_id"],
        "additionalProperties": False,
    },
    outputs={
        "success": "Whether the gateway took the cancel",
        "prompt_id": "The prompt that was cancelled",
        "state": "failed once cancelled, or running while a running prompt stops",
        "response_text": "Plain-language result for the user",
    },
)
def cancel_hosted_gateway_prompt(prompt_id: str = "") -> dict[str, Any]:
    """Ask the app to cancel the prompt on the user's organization's gateway."""
    prompt_id = prompt_id.strip()
    try:
        with HostedGatewayClient.from_account() as client:
            record = client.cancel_prompt(prompt_id)
    except HostedGatewayError as exc:
        return failure_output(exc, tool_name=TOOL_NAME, component=_COMPONENT)
    template = _STOPPING if record.cancel_requested else _CANCELLED
    return {
        "success": True,
        "prompt_id": record.prompt_id,
        "state": record.state,
        "response_text": template.format(prompt_id=record.prompt_id),
    }


__all__ = ["TOOL_NAME", "cancel_hosted_gateway_prompt"]
