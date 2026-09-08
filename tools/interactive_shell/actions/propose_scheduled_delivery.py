"""Record a structured schedule offer awaiting bare yes confirmation."""

from __future__ import annotations

from typing import Any

from core.agent_harness import is_recurring_skill, normalize_skill_name, validate_skill_inputs
from core.agent_harness.spi.session_state import (
    PendingScheduleOffer,
    clear_competing_pending_offers,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from infrastructure.scheduling.scheduler.credentials import requires_explicit_chat_id
from infrastructure.scheduling.scheduler.types import Provider, TaskKind

# Match surfaces.cli.commands.cron: Sentry kinds use `opensre sentry`, not cron add.
_KIND_VALUES = frozenset(
    kind.value
    for kind in TaskKind
    if kind not in (TaskKind.SENTRY_MORNING_DIGEST, TaskKind.SENTRY_UPTIME_WATCH)
)
_PROVIDER_VALUES = frozenset(p.value for p in Provider)

# Morning-report / digest fetches leave these markers on session.history shell rows.
_FETCH_MARKERS = (
    "wttr.in",
    "bbc.co.uk",
    "rss.xml",
    "feeds.",
    "news.google",
    "openweathermap",
)
_BRIEFING_MARKERS = (
    "weather",
    "headline",
    "good morning",
    "briefing",
    "°",
    "humidity",
)


def _history_rows(session: Any, history_start: int = 0) -> list[dict[str, Any]]:
    """Rows this turn appended. Earlier turns are not evidence for this offer."""
    history = getattr(session, "history", None) or []
    return [row for row in history[history_start:] if isinstance(row, dict)]


def _has_fetch_evidence(session: Any, history_start: int = 0) -> bool:
    """True when THIS turn ran a successful shell fetch shaped like weather/news."""
    for item in reversed(_history_rows(session, history_start)[-24:]):
        if item.get("type") != "shell" or not item.get("ok", True):
            continue
        text = str(item.get("text", "")).lower()
        if any(marker in text for marker in _FETCH_MARKERS):
            return True
    return False


def _briefing_looks_real(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 40:
        return False
    lowered = stripped.lower()
    return any(marker in lowered for marker in _BRIEFING_MARKERS)


def _briefing_precondition_error(
    *,
    label: str,
    session: Any,
    history_start: int,
    briefing_text: str,
) -> dict[str, Any] | None:
    """Reject an offer that has no weather/news work behind it."""
    if not _has_fetch_evidence(session, history_start):
        return {
            "ok": False,
            "error": (
                f"No weather/news fetch ran yet this session. For {label}, "
                "run the morning-report shell_run fetches (wttr.in + headlines) "
                "first, compose the briefing, optionally deliver it, THEN call "
                "propose_scheduled_delivery with briefing_text set to that "
                "composed briefing. Do not offer to schedule work that never ran."
            ),
        }
    if not _briefing_looks_real(briefing_text):
        return {
            "ok": False,
            "error": (
                f"briefing_text is required for {label} and must contain the "
                "composed weather + headlines briefing (not empty, not the closer "
                "alone). Pass the same text you showed or delivered to the user."
            ),
        }
    return None


def execute_propose_scheduled_delivery_tool(
    args: dict[str, Any], ctx: ActionToolScope
) -> dict[str, Any]:
    kind = str(args.get("kind", "")).strip().lower()
    cron = " ".join(str(args.get("cron", "")).split())
    timezone = str(args.get("timezone", "UTC")).strip() or "UTC"
    provider = str(args.get("provider", "")).strip().lower()
    chat_id = str(args.get("chat_id", "")).strip()
    prompt = str(args.get("prompt", "") or "").strip()
    briefing_text = str(args.get("briefing_text", "") or "").strip()
    skill_name = normalize_skill_name(str(args.get("skill_name", "") or ""))
    owner = str(args.get("owner", "") or "").strip()
    repo = str(args.get("repo", "") or "").strip()
    branch = str(args.get("branch", "") or "").strip()
    pr_number = str(args.get("pr_number", "") or "").strip()
    city = str(args.get("city", "") or "").strip()

    if kind not in _KIND_VALUES:
        return {
            "ok": False,
            "error": f"unsupported kind {kind!r}",
            "allowed_kinds": sorted(_KIND_VALUES),
        }
    if provider not in _PROVIDER_VALUES:
        return {
            "ok": False,
            "error": f"unsupported provider {provider!r}",
            "allowed_providers": sorted(_PROVIDER_VALUES),
        }
    if len(cron.split()) != 5:
        return {
            "ok": False,
            "error": "cron must have exactly 5 fields (minute hour day month day_of_week)",
        }
    if kind == TaskKind.MANUAL_LOOP.value:
        if not prompt:
            return {"ok": False, "error": "prompt is required for manual_loop."}
    elif prompt:
        return {"ok": False, "error": "prompt is only valid for manual_loop."}
    # Slack is not unconditionally exempt: it can skip an explicit channel only
    # while a webhook supplies one. Asking the scheduler (same check as
    # `/cron add`) instead of hard-coding the provider keeps the offer from
    # storing a task that fires into nothing.
    if (
        provider != Provider.INTERACTIVE_SHELL.value
        and not chat_id
        and requires_explicit_chat_id(provider)
    ):
        return {
            "ok": False,
            "error": (
                f"--chat-id is required for provider {provider}: "
                "it has no configured destination to fall back on."
            ),
        }

    # Refuse the failure mode where "give me a morning report" becomes ONLY a
    # Want-me-to closer: no weather, no headlines, nothing delivered.
    briefing_label = ""
    if kind == TaskKind.RECURRING_SKILL.value:
        if not skill_name or not is_recurring_skill(skill_name):
            return {
                "ok": False,
                "error": (
                    "skill_name is required for recurring_skill and must name a "
                    "skill marked recurring (e.g. 'morning-report')."
                ),
            }
        if skill_name == "morning-report":
            briefing_label = "morning-report"
    elif kind == TaskKind.MANUAL_LOOP.value:
        briefing_label = "manual_loop"
    if briefing_label:
        blocked = _briefing_precondition_error(
            label=briefing_label,
            session=ctx.session,
            history_start=getattr(ctx, "history_start", 0),
            briefing_text=briefing_text,
        )
        if blocked is not None:
            return blocked

    scope_supplied = bool(owner or repo or branch or pr_number)
    skill_inputs: dict[str, str] = {}
    if skill_name == "morning-report":
        if city:
            skill_inputs["city"] = city
    elif skill_name == "github-ci-health":
        if not owner or not repo:
            return {"ok": False, "error": "owner and repo are required for github-ci-health."}
        if branch and pr_number:
            return {"ok": False, "error": "Use either branch or pr_number, not both."}
        if pr_number:
            try:
                if int(pr_number) < 1:
                    raise ValueError
            except ValueError:
                return {"ok": False, "error": "pr_number must be a positive integer."}
        skill_inputs = {"owner": owner, "repo": repo}
        if branch:
            skill_inputs["branch"] = branch
        if pr_number:
            skill_inputs["pr_number"] = pr_number
    elif scope_supplied:
        return {
            "ok": False,
            "error": "owner, repo, branch, and pr_number are only valid for github-ci-health.",
        }
    elif city:
        return {
            "ok": False,
            "error": "city is only valid for morning-report.",
        }

    try:
        skill_inputs = validate_skill_inputs(skill_inputs)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    offer = PendingScheduleOffer(
        kind=kind,
        cron=cron,
        timezone=timezone,
        provider=provider,
        chat_id=chat_id,
        prompt=prompt if kind == TaskKind.MANUAL_LOOP.value else "",
        skill_name=skill_name if kind == TaskKind.RECURRING_SKILL.value else "",
        skill_inputs=skill_inputs,
    )
    ctx.session.pending_schedule_offer = offer
    clear_competing_pending_offers(ctx.session, keep_attr="pending_schedule_offer")
    body = offer.want_me_to_body()
    closer = f"**Want me to:** {body}?"
    # Prefer an explicit response_text so the action driver surfaces the
    # briefing even when the model closing message is only the Want-me-to line.
    response_text = closer if not briefing_text else f"{briefing_text}\n\n{closer}"
    return {
        "ok": True,
        "pending": True,
        "want_me_to": body,
        "closer": closer,
        "response_text": response_text,
        "slash_preview": offer.to_slash_command(),
        "instruction": (
            "Show response_text to the user (briefing + closer). End with the "
            "closer exactly. Do NOT call /cron yet — wait for the user to confirm. "
            "Their yes expands to slash_preview."
        ),
    }


def run_propose_scheduled_delivery(
    *,
    kind: str,
    cron: str,
    provider: str,
    timezone: str = "UTC",
    chat_id: str = "",
    prompt: str = "",
    briefing_text: str = "",
    skill_name: str = "",
    owner: str = "",
    repo: str = "",
    branch: str = "",
    pr_number: str = "",
    city: str = "",
    context: Any,
) -> dict[str, Any]:
    return execute_with_action_context(
        {
            "kind": kind,
            "cron": cron,
            "timezone": timezone,
            "provider": provider,
            "chat_id": chat_id,
            "prompt": prompt,
            "briefing_text": briefing_text,
            "skill_name": skill_name,
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "pr_number": pr_number,
            "city": city,
        },
        context,
        execute_propose_scheduled_delivery_tool,
    )


propose_scheduled_delivery_tool = RegisteredTool(
    name="propose_scheduled_delivery",
    description=(
        "Record a schedule offer the user has NOT yet accepted, and return the "
        "canonical Want me to: closer plus response_text (briefing + closer). "
        "PRECONDITION for recurring_skill morning-report and for manual_loop: "
        "weather/news shell_run fetches must already have succeeded in this "
        "session, prompt must describe the recurring instruction, and "
        "briefing_text must be the composed briefing. This tool "
        "schedules nothing and produces no weather — calling it alone leaves "
        "the user with an empty offer. Do NOT call /cron add until the user "
        "confirms; their yes becomes the slash command."
    ),
    use_cases=[
        (
            "A recurring briefing was just composed (and preferably delivered), and "
            "the user has not said whether they want it to repeat"
        ),
        "The user asked outright to schedule something that already ran this turn",
    ],
    anti_examples=[
        (
            "User asks for a report/briefing/digest — produce and deliver it first; "
            "this tool is the LAST step, never the only response"
        ),
        "User already confirmed — use slash_invoke /cron add instead",
        "Nothing has been fetched or composed yet this turn",
    ],
    input_schema=object_schema(
        properties={
            "kind": string_property(
                description=(
                    "Scheduled task kind, e.g. 'recurring_skill' or 'manual_loop'. "
                    f"Must be a cron-add kind: {', '.join(sorted(_KIND_VALUES))}."
                ),
                min_length=1,
            ),
            "cron": string_property(
                description="5-field cron expression, e.g. '0 8 * * 1-5'.",
                min_length=9,
            ),
            "timezone": string_property(
                description="IANA timezone (default UTC), e.g. 'Europe/Amsterdam'."
            ),
            "provider": string_property(
                description="Delivery provider: slack, telegram, discord, or rocketchat.",
                min_length=1,
            ),
            "chat_id": string_property(
                description=(
                    "Target chat/channel. Optional for slack (webhook-bound). "
                    "Required for telegram/discord/rocketchat."
                ),
            ),
            "prompt": string_property(
                description=(
                    "Required for manual_loop: the instruction to execute on "
                    "every scheduled run. Do not provide it for other task kinds."
                ),
            ),
            "briefing_text": string_property(
                description=(
                    "Required for recurring_skill morning-report and for "
                    "manual_loop: the composed weather + headlines briefing "
                    "already produced for the user. Returned in response_text "
                    "ahead of the Want me to: closer so the user never sees an "
                    "offer without the report."
                ),
            ),
            "skill_name": string_property(
                description=(
                    "Required for recurring_skill: the kebab-case action skill "
                    "to repeat (e.g. 'morning-report')."
                ),
            ),
            "owner": string_property(
                description="Repository owner required by the github-ci-health skill."
            ),
            "repo": string_property(
                description="Repository name required by the github-ci-health skill."
            ),
            "branch": string_property(
                description="Optional github-ci-health branch filter; mutually exclusive with pr_number."
            ),
            "pr_number": string_property(
                description="Optional positive github-ci-health PR number; mutually exclusive with branch."
            ),
            "city": string_property(
                description=(
                    "Optional city for the morning-report skill. Persisted so each "
                    "scheduled tick fetches weather for the requested location."
                ),
            ),
        },
        required=("kind", "cron", "provider"),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_propose_scheduled_delivery,
    tags=("safe", "fast", "no-credentials"),
    side_effect_level=SideEffectLevel.MUTATING,
)

__all__ = [
    "execute_propose_scheduled_delivery_tool",
    "propose_scheduled_delivery_tool",
    "run_propose_scheduled_delivery",
]
