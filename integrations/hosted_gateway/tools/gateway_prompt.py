"""Tool: send one prompt to the organization's hosted gateway and wait for its answer."""

from __future__ import annotations

import time
from typing import Any

from config.constants.hosted_gateway import (
    HOSTED_GATEWAY_PROMPT_POLL_SECONDS,
    HOSTED_GATEWAY_PROMPT_WAIT_SECONDS,
)
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.hosted_gateway.client import (
    HostedGatewayClient,
    HostedGatewayError,
    PromptRecord,
)
from integrations.hosted_gateway.tools.results import SOURCE, failure_output

TOOL_NAME = "ask_hosted_gateway"
_COMPONENT = "integrations.hosted_gateway.tools.gateway_prompt.ask_hosted_gateway"

_STATE_TEXT = {
    "needs_input": (
        "The hosted gateway could not finish without an answer from you. It asked: "
        "{question}\nSend the prompt again with that decided."
    ),
    "failed": "The hosted gateway could not run that prompt ({error}).",
}
_STILL_RUNNING = (
    "The hosted gateway is still working on prompt {prompt_id} after "
    "{waited} seconds. Ask again later with that id to read the result."
)


@tool(
    name=TOOL_NAME,
    source=SOURCE,
    display_name="Ask hosted gateway",
    description=(
        "Send one prompt to the OpenSRE hosted gateway of the signed-in user's organization "
        "(the managed Fargate container that runs CI/CD repair loops remotely) and return its "
        "answer. The gateway runs the prompt unattended: it cannot ask the user anything, so "
        "put every fact it needs into the prompt or the context (repository, branch, task "
        "name). Use it to check what runs there, for example whether a scheduled CI repair "
        "task is running. The OpenSRE app finds the gateway from the signed-in account; no "
        "organization or gateway id is passed. Organization admins only."
    ),
    use_cases=[
        "Ask the hosted gateway which scheduled tasks it runs and whether the CI repair loop is active",
        "Ask the hosted gateway for the state of a repair it ran remotely",
        "Read the result of an earlier hosted gateway prompt by its prompt id",
    ],
    anti_examples=[
        "Questions this machine can answer locally (use the local tools)",
        "Starting or stopping the hosted gateway (use start_hosted_gateway, stop_hosted_gateway)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.EXTERNAL,
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The complete request for the remote gateway, with every fact it needs; "
                    "it cannot ask follow-up questions."
                ),
            },
            "context": {
                "type": "object",
                "description": (
                    "Facts resolved here so the gateway has nothing to ask, as short strings: "
                    "repository, branch, task name."
                ),
                "additionalProperties": {"type": "string"},
            },
            "prompt_id": {
                "type": "string",
                "description": "Read the result of an earlier prompt instead of sending a new one.",
            },
        },
        "additionalProperties": False,
    },
    outputs={
        "success": "Whether the app accepted the request and the gateway reached a settled state",
        "prompt_id": "The prompt's id on the gateway; use it to read the result later",
        "state": "queued, running, done, needs_input or failed",
        "answer": "The gateway's answer when the state is done",
        "question": "What the gateway would have asked when the state is needs_input",
        "response_text": "Plain-language result for the user",
    },
)
def ask_hosted_gateway(
    prompt: str = "",
    context: dict[str, str] | None = None,
    prompt_id: str = "",
) -> dict[str, Any]:
    """Submit the prompt (or look an earlier one up), then wait for the gateway to settle."""
    if not prompt.strip() and not prompt_id.strip():
        return _refusal("Give the hosted gateway a prompt, or a prompt id to read.")
    try:
        with HostedGatewayClient.from_account() as client:
            record = _submit_or_lookup(
                client, prompt.strip(), dict(context or {}), prompt_id.strip()
            )
            record, waited = _wait_until_settled(client, record)
    except HostedGatewayError as exc:
        return failure_output(exc, tool_name=TOOL_NAME, component=_COMPONENT)
    return _outcome(record, waited)


def _submit_or_lookup(
    client: HostedGatewayClient, prompt: str, context: dict[str, str], prompt_id: str
) -> PromptRecord:
    if prompt_id:
        return client.prompt_result(prompt_id)
    return client.send_prompt(prompt, context=context)


def _wait_until_settled(
    client: HostedGatewayClient, record: PromptRecord
) -> tuple[PromptRecord, float]:
    """Poll the app until the gateway settles the prompt or the wait budget is spent."""
    started = time.monotonic()
    current = record
    while not current.settled:
        waited = time.monotonic() - started
        if waited >= HOSTED_GATEWAY_PROMPT_WAIT_SECONDS:
            return current, waited
        time.sleep(HOSTED_GATEWAY_PROMPT_POLL_SECONDS)
        current = client.prompt_result(record.prompt_id)
    return current, time.monotonic() - started


def _outcome(record: PromptRecord, waited: float) -> dict[str, Any]:
    if record.state == "done":
        text = record.answer
    elif record.state in _STATE_TEXT:
        text = _STATE_TEXT[record.state].format(question=record.question, error=record.error)
    else:
        text = _STILL_RUNNING.format(prompt_id=record.prompt_id, waited=int(waited))
    return {
        "success": record.settled,
        "prompt_id": record.prompt_id,
        "state": record.state,
        "answer": record.answer,
        "question": record.question,
        "response_text": text,
    }


def _refusal(text: str) -> dict[str, Any]:
    return {
        "success": False,
        "prompt_id": "",
        "state": "",
        "answer": "",
        "question": "",
        "error": text,
        "response_text": text,
    }


__all__ = ["TOOL_NAME", "ask_hosted_gateway"]
