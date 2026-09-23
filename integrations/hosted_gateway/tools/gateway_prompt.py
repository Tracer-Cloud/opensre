"""Tool: send one prompt to the organization's hosted gateway and wait for its answer."""

from __future__ import annotations

import time
from typing import Any

from config.constants.hosted_gateway import (
    HOSTED_GATEWAY_INTEGRATIONS_PATH,
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
from integrations.hosted_gateway.tools.results import (
    SOURCE,
    failure_output,
    hosted_gateway_available,
)

TOOL_NAME = "ask_hosted_gateway"
_COMPONENT = "integrations.hosted_gateway.tools.gateway_prompt.ask_hosted_gateway"

_STATE_TEXT = {
    "needs_input": (
        "The hosted gateway stopped to ask: {question}\nAsk the user with ask_user_choice "
        "(same question, same options), then call ask_hosted_gateway again with "
        "prompt_id={prompt_id} and answer set to their choice."
    ),
    "failed": "The hosted gateway could not run that prompt ({error}).",
}
_STILL_RUNNING = (
    "The hosted gateway is still working on prompt {prompt_id} after "
    "{waited} seconds. Ask again later with that id to read the result."
)
_FAILED_INTEGRATIONS = (
    "\n\nTools of these integrations returned errors on the hosted gateway: {vendors}. The "
    "gateway uses the organization's integrations, not this machine's credentials. If the "
    "organization has not set them up for the gateway, an admin can do so at {url}; "
    "otherwise the answer above describes the failure."
)


@tool(
    name=TOOL_NAME,
    source=SOURCE,
    display_name="Ask hosted gateway",
    description=(
        "Send one prompt to the OpenSRE hosted gateway of the signed-in user's organization "
        "(the managed Fargate container that runs CI/CD repair loops remotely) and return its "
        "answer. The gateway runs the prompt unattended, so put every fact it needs into the "
        "prompt or the context (repository, branch, task name). When it still needs a "
        "decision, or a tool there needs approval, the result is needs_input with the "
        "question: ask the user, then call this tool again with prompt_id and answer. Use it "
        "to run or check work there, for example whether a scheduled CI repair task is "
        "running. The OpenSRE app finds the gateway from the signed-in account; no "
        "organization or gateway id is passed. Organization admins only."
    ),
    use_cases=[
        "Ask the hosted gateway which scheduled tasks it runs and whether the CI repair loop is active",
        "Ask the hosted gateway for the state of a repair it ran remotely",
        "Read the result of an earlier hosted gateway prompt by its prompt id",
        "Answer a question the hosted gateway asked, or approve a tool it wants to run",
    ],
    anti_examples=[
        "Questions this machine can answer locally (use the local tools)",
        "Starting or stopping the hosted gateway (use start_hosted_gateway, stop_hosted_gateway)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.EXTERNAL,
    is_available=hosted_gateway_available,
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
            "answer": {
                "type": "string",
                "description": (
                    "With prompt_id: the user's answer to the question that prompt stopped "
                    "on (an option label or number; Approve or Deny for an approval)."
                ),
            },
        },
        "additionalProperties": False,
    },
    outputs={
        "success": "Whether the app accepted the request and the gateway reached a settled state",
        "prompt_id": "The prompt's id on the gateway; use it to read the result later",
        "state": "queued, running, done, needs_input or failed",
        "answer": "The gateway's answer when the state is done",
        "question": "What the gateway asked when the state is needs_input",
        "choice": "The question as menu data (title, questions with options) when needs_input",
        "failed_integrations": "Integrations whose tools failed on the gateway, e.g. github",
        "response_text": "Plain-language result for the user",
    },
)
def ask_hosted_gateway(
    prompt: str = "",
    context: dict[str, str] | None = None,
    prompt_id: str = "",
    answer: str = "",
) -> dict[str, Any]:
    """Submit the prompt, answer or look an earlier one up, then wait for the gateway to settle."""
    if not prompt.strip() and not prompt_id.strip():
        return _refusal("Give the hosted gateway a prompt, or a prompt id to read.")
    if answer.strip() and not prompt_id.strip():
        return _refusal("An answer needs the prompt_id of the question it answers.")
    try:
        with HostedGatewayClient.from_account() as client:
            record = _submit_or_lookup(
                client, prompt.strip(), dict(context or {}), prompt_id.strip(), answer.strip()
            )
            record, waited = _wait_until_settled(client, record)
            integrations_url = f"{client.app_url}{HOSTED_GATEWAY_INTEGRATIONS_PATH}"
    except HostedGatewayError as exc:
        return failure_output(exc, tool_name=TOOL_NAME, component=_COMPONENT)
    return _outcome(record, waited, integrations_url)


def _submit_or_lookup(
    client: HostedGatewayClient,
    prompt: str,
    context: dict[str, str],
    prompt_id: str,
    answer: str,
) -> PromptRecord:
    if prompt_id and answer:
        return client.answer_prompt(prompt_id, answer)
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


def _outcome(record: PromptRecord, waited: float, integrations_url: str) -> dict[str, Any]:
    if record.state == "done":
        text = record.answer
    elif record.state in _STATE_TEXT:
        text = _STATE_TEXT[record.state].format(
            question=record.question, error=record.error, prompt_id=record.prompt_id
        )
    else:
        text = _STILL_RUNNING.format(prompt_id=record.prompt_id, waited=int(waited))
    if record.failed_integrations:
        vendors = ", ".join(record.failed_integrations)
        text = text + _FAILED_INTEGRATIONS.format(vendors=vendors, url=integrations_url)
    return {
        "success": record.settled,
        "prompt_id": record.prompt_id,
        "state": record.state,
        "answer": record.answer,
        "question": record.question,
        "choice": _choice_data(record),
        "failed_integrations": list(record.failed_integrations),
        "response_text": text,
    }


def _choice_data(record: PromptRecord) -> dict[str, Any] | None:
    if record.choice is None:
        return None
    questions = [
        {"title": q.title, "options": list(q.options), "multi_select": q.multi_select}
        for q in record.choice.questions
    ]
    return {
        "title": record.choice.title,
        "questions": questions,
        "custom_answer": record.choice.custom_answer,
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
