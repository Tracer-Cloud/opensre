"""Tool: send one prompt to the organization's hosted gateway and wait for its answer.

When the gateway stops to ask, the question is parked on this shell's own menu and the
user's selection, never a model argument, is what goes back as the answer.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from http import HTTPStatus
from typing import Any

from config.constants.gateway import PROMPT_CONVERSATION_NEW
from config.constants.github import GITHUB_TOKEN_CHECKLIST
from config.constants.hosted_gateway import (
    HOSTED_GATEWAY_INTEGRATIONS_PATH,
    HOSTED_GATEWAY_PROMPT_POLL_SECONDS,
    HOSTED_GATEWAY_PROMPT_WAIT_SECONDS,
    HOSTED_GATEWAY_QUEUE_NOTICE_SECONDS,
    HOSTED_GATEWAY_SUBMIT_RETRY_BUDGET_SECONDS,
    HOSTED_GATEWAY_SUBMIT_RETRY_DELAYS_SECONDS,
    HOSTED_GATEWAY_UNANSWERED_GRACE_SECONDS,
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
    TRANSIENT_ERRORS,
    HostedGatewayClient,
    HostedGatewayError,
    PromptChoice,
    PromptRecord,
)
from integrations.hosted_gateway.tools.results import (
    SOURCE,
    cause_sentence,
    failure_output,
    hosted_gateway_available,
)

TOOL_NAME = "ask_hosted_gateway"
_COMPONENT = "integrations.hosted_gateway.tools.gateway_prompt.ask_hosted_gateway"
_CHOOSE_COMMAND = "/choose"
_HOSTED_PROMPT_INTERACTION_PREFIX = "hosted_prompt:"
_QUEUED_NOTICE = "waiting for a free slot on the gateway (another conversation is using it)"
_UNANSWERED_NOTICE = "the gateway is not answering right now; still waiting for it"
#: The one vendor whose credential refusals have a known, ordered fix.
_GITHUB_VENDOR = "github"
_GITHUB_REFUSAL_LEAD = (
    "Only if GitHub refused the credential (HTTP 401 or 403; a rate limit or an unavailable "
    "repository needs no token change): "
)
_GITHUB_REFUSAL_CODES = (int(HTTPStatus.UNAUTHORIZED), int(HTTPStatus.FORBIDDEN))

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
    "interrupted": (
        "The hosted gateway restarted before it finished that prompt, so it did not complete. "
        "Send it again."
    ),
    "cancelled": "That prompt was cancelled before it finished.",
    "unknown_conversation": (
        "The hosted gateway has no such conversation for this user. Send the prompt without "
        "a conversation, or with conversation=new."
    ),
    "conversation_waiting": (
        "That conversation is waiting for the answer to its question. Answer the question "
        "first, or send the prompt with conversation=new."
    ),
}
#: A follow-up the gateway could not use; it reopened the question on the original prompt.
_ANSWER_NOT_USED = {
    "invalid_answer": "That answer did not match the question's options; the question opens again. ",
    "interrupted": "The hosted gateway restarted before it used that answer; the question opens again. ",
    "cancelled": "That answer was cancelled; the question opens again. ",
}
#: The prompt id stays in the user's line: it is all the resumed turn keeps of this result.
_ASKING_IN_SHELL = (
    "The hosted gateway needs your decision; the menu opens now. Your selection goes back "
    "to its prompt {prompt_id}."
)
#: Introduces what the gateway wrote before its question.
_GATEWAY_REPORTED = "The hosted gateway reported:"
#: For the model only: how the parked question continues once the user has answered.
_ASKING_IN_SHELL_INSTRUCTIONS = (
    "The question is parked on the shell's menu; the user answers it there. Once they have "
    "answered, call ask_hosted_gateway again with prompt_id={prompt_id} and no prompt; their "
    "selection is sent as the answer. Do not repeat the question and do not answer it yourself."
)
_ASKING_WITHOUT_SHELL = (
    "The hosted gateway stopped to ask: {question}\nAnswer it from the interactive shell "
    "(`opensre`) by asking about prompt {prompt_id} there."
)
_STILL_RUNNING = (
    "The hosted gateway is still working on prompt {prompt_id} after "
    "{waited} seconds. Ask again later with that id to read the result."
)
_LOST_CONTACT = (
    "Lost contact with the hosted gateway while it worked on prompt {prompt_id}. Ask again "
    "with that id in a minute; if the gateway restarted meanwhile, the prompt reads as "
    "interrupted and has to be sent again."
)
#: A failed read or answer of a prompt the gateway already holds: the id is the way back.
_ASK_AGAIN_ABOUT = (
    "Ask about prompt {prompt_id} again in a minute: it shows the answer the gateway "
    "already took, if any, so nothing runs twice."
)
#: A fresh prompt whose response was lost: resending under the same request id is safe.
_SAFE_TO_RESEND = (
    "The gateway may not have taken the prompt. Sending it again is safe: it will not run twice."
)
#: For the model only: how to resend so the gateway recognizes the same prompt.
_RESEND_INSTRUCTIONS = (
    "To send it again, call ask_hosted_gateway with the same prompt and facts and "
    "request_id={request_id}; a new request_id could run the prompt twice."
)
_FAILED_INTEGRATIONS = (
    "The hosted gateway's {vendors} integration failed during this request. The gateway "
    "uses the organization's {vendors} credential from {url}, not this machine's: if that "
    "credential is invalid or cannot reach the repository, an admin updates it there and "
    "the gateway picks the new one up within a minute. {next_step}\n\n"
)
#: For the model only: the failed integration is the first thing the user hears about.
_FAILED_INTEGRATIONS_INSTRUCTIONS = (
    "Tell the user about the failed {vendors} integration first, in plain words, before "
    "anything else in the answer."
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
        "example whether a scheduled CI repair task is running. A prompt continues the "
        "user's own gateway conversation, one prompt at a time; conversation=new starts a "
        "separate one that runs beside it. The OpenSRE app finds the gateway from the "
        "signed-in account; no organization or gateway id is passed."
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
            "request_id": {
                "type": "string",
                "description": (
                    "Only when resending a prompt whose earlier send failed: the request_id "
                    "that failure returned, so the gateway runs the prompt at most once."
                ),
            },
            "conversation": {
                "type": "string",
                "description": (
                    "Leave empty to continue the user's own gateway conversation. 'new' "
                    "starts a separate conversation that runs at the same time as the "
                    "user's others; use it for an unrelated investigation. A "
                    "conversation_id from an earlier result continues that conversation."
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
        "conversation_id": "The conversation the prompt ran on; pass it as conversation to continue it",
        "cause_code": "The app's specific reason when a prompt was refused, empty otherwise",
        "response_text": "Plain-language result for the user",
        "instructions": "What to do next with a parked question or a failed integration; not for the user",
    },
)
def ask_hosted_gateway(
    prompt: str = "",
    facts: dict[str, str] | None = None,
    prompt_id: str = "",
    request_id: str = "",
    conversation: str = "",
    context: Any = None,
) -> dict[str, Any]:
    """Submit the prompt or continue an earlier one, then wait for the gateway to settle."""
    if not prompt.strip() and not prompt_id.strip():
        return _refusal("Give the hosted gateway a prompt, or a prompt id to read.")
    conversation = conversation.strip()
    if not _valid_conversation(conversation):
        return _refusal(
            "conversation must be empty, 'new', or a conversation_id from an earlier result."
        )
    scope = _shell_scope(context)
    in_flight = ""
    # One id per logical submission, reused by a resend, so the gateway queues it once.
    request_id = request_id.strip() or uuid.uuid4().hex
    try:
        with HostedGatewayClient.from_account() as client:
            relay = _ProgressRelay(context)
            record, sent_at, skip_recorded = _submit_riding_out_restarts(
                client,
                relay,
                prompt.strip(),
                dict(facts or {}),
                prompt_id.strip(),
                scope,
                request_id,
                conversation,
            )
            in_flight = record.prompt_id
            if skip_recorded:
                relay.skip_recorded(record)
            record, waited = _wait_until_settled(client, record, relay, sent_at=sent_at)
            parent_id = record.parent_prompt_id or prompt_id.strip()
            is_follow_up = bool(parent_id) and parent_id != record.prompt_id
            not_used = _answer_not_used(record) if is_follow_up else ""
            if not_used:
                # The gateway reopened the question on the original prompt; show it again.
                record = client.prompt_result(parent_id)
            integrations_url = f"{client.app_url}{HOSTED_GATEWAY_INTEGRATIONS_PATH}"
    except HostedGatewayError as exc:
        return _failure(exc, in_flight, known=prompt_id.strip(), request_id=request_id)
    outcome = _outcome(record, waited, integrations_url, scope)
    if not_used and record.state == "needs_input":
        outcome["response_text"] = not_used + outcome["response_text"]
    return outcome


def _waiting_notice(exc: HostedGatewayError) -> str:
    """A mid-wait poll failure: the accepted prompt is still being awaited.

    The cause names why this read failed. It follows the waiting line so a
    sentence that says to try again is not the only thing the user sees while
    the tool keeps polling the prompt it already sent.
    """
    cause = cause_sentence(exc)
    if not cause:
        return _UNANSWERED_NOTICE
    return f"{_UNANSWERED_NOTICE}. {cause}"


def _failure(
    exc: HostedGatewayError, in_flight: str, *, known: str, request_id: str
) -> dict[str, Any]:
    """A failed call's result; a transient failure says how to retry without running twice.

    Once a prompt was accepted (``in_flight``) or the call named one (``known``),
    the result keeps that id: reading it shows what the gateway already took. A
    fresh prompt that got no id keeps its ``request_id`` instead: resent with
    it, the gateway queues the prompt at most once.
    """
    out = failure_output(exc, tool_name=TOOL_NAME, component=_COMPONENT)
    if exc.code not in TRANSIENT_ERRORS:
        return out
    prompt_id = in_flight or known
    if in_flight:
        text = _LOST_CONTACT.format(prompt_id=in_flight)
        cause = cause_sentence(exc)
        if cause:
            text = f"{text} {cause}"
    elif prompt_id:
        text = f"{out['response_text']} {_ASK_AGAIN_ABOUT.format(prompt_id=prompt_id)}"
    else:
        text = f"{out['response_text']} {_SAFE_TO_RESEND}"
    result = {**out, "error": text, "response_text": text}
    if prompt_id:
        result["prompt_id"] = prompt_id
    else:
        result["request_id"] = request_id
        result["instructions"] = _RESEND_INSTRUCTIONS.format(request_id=request_id)
    return result


def _answer_not_used(record: PromptRecord) -> str:
    """The lead line for a failed follow-up whose question takes an answer again, else empty."""
    if record.state != "failed":
        return ""
    return _ANSWER_NOT_USED.get(record.error, "")


def _submit_riding_out_restarts(
    client: HostedGatewayClient,
    relay: _ProgressRelay,
    prompt: str,
    facts: dict[str, str],
    prompt_id: str,
    scope: ActionToolScope | None,
    request_id: str,
    conversation: str,
) -> tuple[PromptRecord, float, bool]:
    """``_submit_or_continue``, retried with a bounded backoff while the gateway is not answering.

    Safe to repeat: every attempt carries the same ``request_id``, so the gateway
    queues a prompt or takes an answer at most once, and a read changes nothing.
    Only transient failures are retried, and no retry starts once
    ``HOSTED_GATEWAY_SUBMIT_RETRY_BUDGET_SECONDS`` has elapsed; the last failure
    is raised as is.
    """
    deadline = time.monotonic() + HOSTED_GATEWAY_SUBMIT_RETRY_BUDGET_SECONDS
    delays = iter(HOSTED_GATEWAY_SUBMIT_RETRY_DELAYS_SECONDS)
    noticed = False
    while True:
        try:
            return _submit_or_continue(
                client, prompt, facts, prompt_id, scope, request_id, conversation
            )
        except HostedGatewayError as exc:
            delay = next(delays, None)
            if (
                exc.code not in TRANSIENT_ERRORS
                or delay is None
                or time.monotonic() + delay >= deadline
            ):
                raise
            if not noticed:
                relay.note(_waiting_notice(exc))
                noticed = True
            time.sleep(delay)


def _submit_or_continue(
    client: HostedGatewayClient,
    prompt: str,
    facts: dict[str, str],
    prompt_id: str,
    scope: ActionToolScope | None,
    request_id: str,
    conversation: str = "",
) -> tuple[PromptRecord, float, bool]:
    """Send a new prompt, or read an earlier one and pass the user's answer on if they gave one.

    The timestamp is when the request that could queue the prompt left this
    machine, taken right before that call. The bool is true when ``record`` is
    a read of an existing prompt, so progress already on it must not be replayed.
    A question that already took an answer leads to that answer's follow-up, so
    an answer whose response was lost is followed rather than sent again.
    """
    if not prompt_id:
        sent_at = time.monotonic()
        record = client.send_prompt(
            prompt, context=facts, request_id=request_id, conversation=conversation
        )
        return record, sent_at, False
    fetched_at = time.monotonic()
    record = client.prompt_result(prompt_id)
    if record.state == "needs_input" and record.answered_by:
        follow_up = client.prompt_result(record.answered_by)
        if not _answer_not_used(follow_up):
            return follow_up, fetched_at, True
    if record.state != "needs_input" or record.choice is None:
        return record, fetched_at, True
    answer = _answer_from_turn(scope, record.choice)
    if answer is None:
        return record, fetched_at, True
    sent_at = time.monotonic()
    return client.answer_prompt(prompt_id, answer, request_id=request_id), sent_at, False


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
    client: HostedGatewayClient,
    record: PromptRecord,
    relay: _ProgressRelay,
    *,
    sent_at: float | None = None,
) -> tuple[PromptRecord, float]:
    """Poll the app until the gateway settles the prompt or the wait budget is spent.

    ``sent_at`` is when the prompt left this machine; the queue notice counts
    from there, so a slow submission does not delay it. A read nobody answered
    is polled again until the gateway has been silent for the grace period;
    then that failure is raised.
    """
    started = time.monotonic()
    queued_since = started if sent_at is None else sent_at
    current = record
    relay.show(current)
    queue_noticed = False
    silent_since: float | None = None
    while not current.settled:
        waited = time.monotonic() - started
        if waited >= HOSTED_GATEWAY_PROMPT_WAIT_SECONDS:
            return current, waited
        in_queue = time.monotonic() - queued_since
        still_queued = current.state == "queued" and in_queue >= HOSTED_GATEWAY_QUEUE_NOTICE_SECONDS
        if still_queued and not queue_noticed:
            relay.note(_QUEUED_NOTICE)
            queue_noticed = True
        time.sleep(HOSTED_GATEWAY_PROMPT_POLL_SECONDS)
        try:
            current = client.prompt_result(record.prompt_id)
        except HostedGatewayError as exc:
            if exc.code not in TRANSIENT_ERRORS:
                raise
            now = time.monotonic()
            if silent_since is None:
                silent_since = now
                relay.note(_waiting_notice(exc))
            elif now - silent_since >= HOSTED_GATEWAY_UNANSWERED_GRACE_SECONDS:
                raise
            continue
        silent_since = None
        relay.show(current)
    return current, time.monotonic() - started


class _ProgressRelay:
    """Hands each new progress line of the gateway to the shell, once, as a tool update."""

    def __init__(self, context: Any) -> None:
        self._emit = getattr(context, "emit_update", None)
        self._last_index = -1

    def skip_recorded(self, record: PromptRecord) -> None:
        """Ignore progress lines already present on a fetched prompt."""
        if not record.progress:
            return
        recorded = max(line.index for line in record.progress)
        if recorded > self._last_index:
            self._last_index = recorded

    def show(self, record: PromptRecord) -> None:
        if self._emit is None:
            return
        for line in record.progress:
            if line.index <= self._last_index:
                continue
            self._last_index = line.index
            self._emit({"progress": line.text, "kind": line.kind})

    def note(self, text: str) -> None:
        """A line about the wait itself, not relayed from the gateway."""
        if self._emit is not None:
            self._emit({"progress": text})


def _outcome(
    record: PromptRecord, waited: float, integrations_url: str, scope: ActionToolScope | None
) -> dict[str, Any]:
    instructions: list[str] = []
    if record.state == "done":
        text = record.answer
    elif record.state == "needs_input":
        text, parked = _ask_here(record, scope)
        report = record.answer.strip()
        if report:
            # The menu line, with its prompt id, leads: the shell previews only the head.
            text = f"{text}\n\n{_relayed_report(report)}"
        if parked:
            instructions.append(_ASKING_IN_SHELL_INSTRUCTIONS.format(prompt_id=record.prompt_id))
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
        if _GITHUB_VENDOR in record.failed_integrations and _credential_was_refused(text):
            # The gateway reports the vendor, not the error kind. The token
            # checklist is only for an HTTP 401 or 403 named in the answer.
            hint = f"{hint.rstrip()} {_GITHUB_REFUSAL_LEAD}{GITHUB_TOKEN_CHECKLIST}\n\n"
        text = hint + text
        instructions.insert(0, _FAILED_INTEGRATIONS_INSTRUCTIONS.format(vendors=vendors))
    return {
        "success": record.settled,
        "prompt_id": record.prompt_id,
        "state": record.state,
        "question": record.question,
        "choice": _choice_data(record),
        "failed_integrations": list(record.failed_integrations),
        "conversation_id": record.conversation_id,
        "cause_code": "",
        "response_text": text,
        "instructions": " ".join(instructions),
    }


def _valid_conversation(conversation: str) -> bool:
    """Empty, ``new``, or a conversation id in the canonical form the gateway hands out."""
    if conversation in ("", PROMPT_CONVERSATION_NEW):
        return True
    try:
        return str(uuid.UUID(conversation)) == conversation
    except ValueError:
        return False


def _credential_was_refused(text: str) -> bool:
    """True when the answer names an HTTP 401 or 403 credential refusal."""
    return any(re.search(rf"\b{code}\b", text) for code in _GITHUB_REFUSAL_CODES)


def _failure_text(error: str) -> str:
    known = _FAILURE_TEXT.get(error)
    if known is not None:
        return known
    return _STATE_TEXT["failed"].format(error=error)


def _relayed_report(report: str) -> str:
    """The gateway's reply before its question, shaped so the shell keeps the menu line.

    The shell drops a tool text that reads as data (it opens with a bracket or carries
    two ``":``) or as a repeated outcome report, and the menu line with the prompt id
    would go with it. A label, a block quote, and single quotes keep the reply as prose.
    """
    lines = report.replace('"', "'").splitlines()
    quoted = "\n".join(f"> {line}" if line.strip() else ">" for line in lines)
    return f"{_GATEWAY_REPORTED}\n{quoted}"


def _ask_here(record: PromptRecord, scope: ActionToolScope | None) -> tuple[str, bool]:
    """Park the gateway's question on this shell's menu so the user answers it, not the model.

    Returns the words for the user and whether the question was parked.
    """
    session = getattr(scope, "session", None)
    if record.choice is None or session is None:
        text = _ASKING_WITHOUT_SHELL.format(question=record.question, prompt_id=record.prompt_id)
        return text, False
    session.pending_user_choice = _local_choice(record.prompt_id, record.choice)
    set_auto_command(session, _CHOOSE_COMMAND)
    terminal = session_terminal(session)
    if terminal is not None:
        terminal.awaiting_handoff_answer = True
    return _ASKING_IN_SHELL.format(prompt_id=record.prompt_id), True


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
        "cause_code": "",
        "error": text,
        "response_text": text,
    }


__all__ = ["TOOL_NAME", "ask_hosted_gateway"]
