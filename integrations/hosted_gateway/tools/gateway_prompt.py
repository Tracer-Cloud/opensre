"""Tool: send one prompt to the organization's hosted gateway and wait for its answer.

When the gateway stops to ask, the question is parked on this shell's own menu and the
user's selection, never a model argument, is what goes back as the answer.
"""

from __future__ import annotations

import json
import time
from typing import Any

from config.constants.hosted_gateway import (
    HOSTED_GATEWAY_INTEGRATIONS_PATH,
    HOSTED_GATEWAY_PROMPT_POLL_SECONDS,
    HOSTED_GATEWAY_PROMPT_WAIT_SECONDS,
)
from core.agent_harness.spi.handoff import AskUserQuestion, parse_ask_user_answers, question_key
from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.tools import ActionToolScope, action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.hosted_gateway.client import (
    HostedGatewayClient,
    HostedGatewayError,
    PromptChoice,
    PromptRecord,
)
from integrations.hosted_gateway.tools.results import (
    SOURCE,
    failure_output,
    hosted_gateway_available,
)

TOOL_NAME = "ask_hosted_gateway"
_COMPONENT = "integrations.hosted_gateway.tools.gateway_prompt.ask_hosted_gateway"
_CHOOSE_COMMAND = "/choose"
_HOSTED_PROMPT_INTERACTION_PREFIX = "hosted_prompt:"

_STATE_TEXT = {
    "failed": "The hosted gateway could not run that prompt ({error}).",
}
#: Plain words for the gateway's failure codes; anything else keeps the code.
_FAILURE_TEXT = {
    "not_admitted": (
        "The hosted gateway was busy with another conversation for too long and did not "
        "take the prompt. Send it again in a moment."
    ),
    "credits_denied": (
        "The organization has no hosted credits left, so the gateway refused the prompt. "
        "Top up in the OpenSRE app, then send it again."
    ),
    "turn_failed": (
        "The hosted gateway hit an error while running the prompt. Send it again; if it "
        "repeats, the gateway's logs have the detail."
    ),
    "invalid_answer": (
        "That answer did not match the question's options. Ask again about the original "
        "prompt id to reopen its menu, then answer from the menu."
    ),
}
_ANSWER_REJECTED = "That answer did not match the question's options; the question opens again. "
_ASKING_IN_SHELL = (
    "The hosted gateway needs a decision from the user; the menu opens now. Once they have "
    "answered, call ask_hosted_gateway again with prompt_id={prompt_id}; their selection is "
    "sent as the answer. Do not repeat the question."
)
_ASKING_WITHOUT_SHELL = (
    "The hosted gateway stopped to ask: {question}\nAnswer it from the interactive shell "
    "(`opensre`) by asking about prompt {prompt_id} there."
)
_STILL_RUNNING = (
    "The hosted gateway is still working on prompt {prompt_id} after "
    "{waited} seconds. Ask again later with that id to read the result."
)
_FAILED_INTEGRATIONS = (
    "The hosted gateway could not use the organization's {vendors} integration. It uses the "
    "organization's credentials from {url}, not this machine's: an admin fixes or replaces "
    "them there and the gateway restarts with the new ones. {next_step} Tell the user this "
    "first, in plain words.\n\n"
)
#: What may follow a fixed credential, by state: never a blind re-send of work already done.
_FAILED_INTEGRATION_NEXT_STEP = {
    "needs_input": (
        "Then continue this prompt through its menu (it usually offers a retry); do not send "
        "the prompt again, the gateway would start the work over."
    ),
    "failed": "Then the prompt can be sent again; nothing of it ran to completion.",
    "done": (
        "The answer that follows stands for what did run; ask again only for what the "
        "failed integration should have done, not for the whole request."
    ),
}


@tool(
    name=TOOL_NAME,
    source=SOURCE,
    display_name="Ask hosted gateway",
    description=(
        "Send one prompt to the OpenSRE hosted gateway of the signed-in user's organization "
        "(the managed Fargate container that runs CI/CD repair loops remotely) and return its "
        "answer. The gateway runs the prompt unattended, so put every fact it needs into the "
        "prompt or the facts (repository, branch, task name). When it still needs a decision, "
        "or a tool there needs approval, the result is needs_input and the question opens as "
        "a menu in this shell; after the user answers, call this tool again with the same "
        "prompt_id and their selection is sent. Use it to run or check work there, for "
        "example whether a scheduled CI repair task is running. The OpenSRE app finds the "
        "gateway from the signed-in account; no organization or gateway id is passed. "
        "Organization admins only."
    ),
    use_cases=[
        "Ask the hosted gateway which scheduled tasks it runs and whether the CI repair loop is active",
        "Ask the hosted gateway for the state of a repair it ran remotely",
        "Read the result of an earlier hosted gateway prompt by its prompt id",
        "Continue a hosted gateway prompt after the user answered its question in the menu",
    ],
    anti_examples=[
        "Questions this machine can answer locally (use the local tools)",
        "Starting or stopping the hosted gateway (use start_hosted_gateway, stop_hosted_gateway)",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.EXTERNAL,
    is_available=hosted_gateway_available,
    accepts_runtime_context=True,
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "The complete request for the remote gateway, with every fact it needs; "
                    "it cannot ask follow-up questions in the same turn."
                ),
            },
            "facts": {
                "type": "object",
                "description": (
                    "Facts resolved here so the gateway has nothing to ask, as short strings: "
                    "repository, branch, task name."
                ),
                "additionalProperties": {"type": "string"},
            },
            "prompt_id": {
                "type": "string",
                "description": (
                    "Read the result of an earlier prompt instead of sending a new one. When "
                    "the user has just answered that prompt's question in the menu, their "
                    "selection is sent along."
                ),
            },
        },
        "additionalProperties": False,
    },
    outputs={
        "success": "Whether the app accepted the request and the gateway reached a settled state",
        "prompt_id": "The prompt's id on the gateway; use it to read the result later",
        "state": "queued, running, done, needs_input or failed",
        "question": "What the gateway asked when the state is needs_input",
        "choice": "The question as menu data (title, note, questions with options) when needs_input",
        "failed_integrations": "Integrations whose tools failed on the gateway, e.g. github",
        "response_text": "Plain-language result for the user",
    },
)
def ask_hosted_gateway(
    prompt: str = "",
    facts: dict[str, str] | None = None,
    prompt_id: str = "",
    context: Any = None,
) -> dict[str, Any]:
    """Submit the prompt or continue an earlier one, then wait for the gateway to settle."""
    if not prompt.strip() and not prompt_id.strip():
        return _refusal("Give the hosted gateway a prompt, or a prompt id to read.")
    scope = _shell_scope(context)
    try:
        with HostedGatewayClient.from_account() as client:
            record = _submit_or_continue(
                client, prompt.strip(), dict(facts or {}), prompt_id.strip(), scope
            )
            record, waited = _wait_until_settled(client, record, _ProgressRelay(context))
            parent_id = record.parent_prompt_id or prompt_id.strip()
            rejected = _answer_was_rejected(record) and bool(parent_id)
            if rejected:
                # The gateway reopened the question on the original prompt; show it again.
                record = client.prompt_result(parent_id)
            integrations_url = f"{client.app_url}{HOSTED_GATEWAY_INTEGRATIONS_PATH}"
    except HostedGatewayError as exc:
        return failure_output(exc, tool_name=TOOL_NAME, component=_COMPONENT)
    outcome = _outcome(record, waited, integrations_url, scope)
    if rejected and record.state == "needs_input":
        outcome["response_text"] = _ANSWER_REJECTED + outcome["response_text"]
    return outcome


def _answer_was_rejected(record: PromptRecord) -> bool:
    return record.state == "failed" and record.error == "invalid_answer"


def _submit_or_continue(
    client: HostedGatewayClient,
    prompt: str,
    facts: dict[str, str],
    prompt_id: str,
    scope: ActionToolScope | None,
) -> PromptRecord:
    """Send a new prompt, or read an earlier one and pass the user's answer on if they gave one."""
    if not prompt_id:
        return client.send_prompt(prompt, context=facts)
    record = client.prompt_result(prompt_id)
    if record.state != "needs_input" or record.choice is None:
        return record
    answer = _answer_from_turn(scope, record.choice)
    if answer is None:
        return record
    return client.answer_prompt(prompt_id, answer)


def _answer_from_turn(scope: ActionToolScope | None, choice: PromptChoice) -> str | None:
    """The user's selections for the gateway's questions, taken from this turn's message.

    The shell writes that message from the menu the user answered, so the model cannot
    supply an answer of its own. ``None`` until every question has a selection.
    """
    if scope is None:
        return None
    message = getattr(scope, "turn_user_message", "") or ""
    given = {question_key(asked): answer for asked, answer in parse_ask_user_answers(message)}
    answers: dict[str, str] = {}
    for question in choice.questions:
        answer = given.get(question_key(question.title))
        if answer is None:
            return None
        answers[question.title] = answer
    if len(answers) == 1:
        return next(iter(answers.values()))
    return json.dumps(answers, ensure_ascii=False)


def _wait_until_settled(
    client: HostedGatewayClient, record: PromptRecord, relay: _ProgressRelay
) -> tuple[PromptRecord, float]:
    """Poll the app until the gateway settles the prompt or the wait budget is spent."""
    started = time.monotonic()
    current = record
    relay.show(current)
    while not current.settled:
        waited = time.monotonic() - started
        if waited >= HOSTED_GATEWAY_PROMPT_WAIT_SECONDS:
            return current, waited
        time.sleep(HOSTED_GATEWAY_PROMPT_POLL_SECONDS)
        current = client.prompt_result(record.prompt_id)
        relay.show(current)
    return current, time.monotonic() - started


class _ProgressRelay:
    """Hands each new progress line of the gateway to the shell, once, as a tool update."""

    def __init__(self, context: Any) -> None:
        self._emit = getattr(context, "emit_update", None)
        self._last_index = -1

    def show(self, record: PromptRecord) -> None:
        if self._emit is None:
            return
        for line in record.progress:
            if line.index <= self._last_index:
                continue
            self._last_index = line.index
            self._emit({"progress": line.text})


def _outcome(
    record: PromptRecord, waited: float, integrations_url: str, scope: ActionToolScope | None
) -> dict[str, Any]:
    if record.state == "done":
        text = record.answer
    elif record.state == "needs_input":
        text = _ask_here(record, scope)
    elif record.state in _STATE_TEXT:
        text = _failure_text(record.error)
    else:
        text = _STILL_RUNNING.format(prompt_id=record.prompt_id, waited=int(waited))
    if record.failed_integrations:
        vendors = ", ".join(record.failed_integrations)
        next_step = _FAILED_INTEGRATION_NEXT_STEP.get(record.state, "")
        hint = _FAILED_INTEGRATIONS.format(
            vendors=vendors, url=integrations_url, next_step=next_step
        )
        text = hint + text
    return {
        "success": record.settled,
        "prompt_id": record.prompt_id,
        "state": record.state,
        "question": record.question,
        "choice": _choice_data(record),
        "failed_integrations": list(record.failed_integrations),
        "response_text": text,
    }


def _failure_text(error: str) -> str:
    known = _FAILURE_TEXT.get(error)
    if known is not None:
        return known
    return _STATE_TEXT["failed"].format(error=error)


def _ask_here(record: PromptRecord, scope: ActionToolScope | None) -> str:
    """Park the gateway's question on this shell's menu so the user answers it, not the model."""
    session = getattr(scope, "session", None)
    if record.choice is None or session is None:
        return _ASKING_WITHOUT_SHELL.format(question=record.question, prompt_id=record.prompt_id)
    session.pending_user_choice = _local_choice(record.prompt_id, record.choice)
    set_auto_command(session, _CHOOSE_COMMAND)
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.awaiting_handoff_answer = True
    return _ASKING_IN_SHELL.format(question=record.question, prompt_id=record.prompt_id)


def _local_choice(prompt_id: str, choice: PromptChoice) -> PendingUserChoice:
    questions = tuple(
        AskUserQuestion(label="", title=q.title, options=q.options, multi_select=q.multi_select)
        for q in choice.questions
    )
    first = questions[0] if questions else None
    return PendingUserChoice(
        title=choice.title,
        options=first.options if first is not None else (),
        questions=questions if len(questions) > 1 else (),
        multi_select=first.multi_select if first is not None else False,
        note=choice.note,
        custom_answer=choice.custom_answer,
        interaction_id=f"{_HOSTED_PROMPT_INTERACTION_PREFIX}{prompt_id}",
    )


def _shell_scope(context: Any) -> ActionToolScope | None:
    if context is None:
        return None
    try:
        return action_context_from_agent_context(context)
    except RuntimeError:
        return None


def _choice_data(record: PromptRecord) -> dict[str, Any] | None:
    if record.choice is None:
        return None
    questions = [
        {"title": q.title, "options": list(q.options), "multi_select": q.multi_select}
        for q in record.choice.questions
    ]
    return {
        "title": record.choice.title,
        "note": record.choice.note,
        "questions": questions,
        "custom_answer": record.choice.custom_answer,
    }


def _refusal(text: str) -> dict[str, Any]:
    return {
        "success": False,
        "prompt_id": "",
        "state": "",
        "question": "",
        "error": text,
        "response_text": text,
    }


__all__ = ["TOOL_NAME", "ask_hosted_gateway"]
