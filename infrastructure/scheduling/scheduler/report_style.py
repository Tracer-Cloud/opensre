"""How every scheduled loop writes its reply, whatever it reports on.

A loop's prompt says *what* to report; this guide says *how*, and it is added
to every tick (:func:`report_style_block`) so it cannot be skipped. The reply
is portable Markdown in one fixed shape -- title, one-line headline, a few
headed sections of short bullets -- which each delivery channel then renders
natively: Slack Block Kit, Telegram HTML, an HTML email
(:mod:`infrastructure.text.report_layout`). The model never writes Slack or
Telegram syntax itself.

The shape follows reports people actually read in chat: the answer first,
one fact per line, numbers with their comparison, owners and links on every
action, and no narration of how the data was gathered.
"""

from __future__ import annotations

from collections.abc import Iterable

from infrastructure.scheduling.scheduler.types import Provider

#: Rough length a destination reads well at (characters of Markdown).
_LENGTH_BUDGET: dict[Provider, int] = {
    Provider.SLACK: 1800,
    Provider.TELEGRAM: 3000,
    Provider.EMAIL: 4000,
    Provider.INTERACTIVE_SHELL: 3000,
}
_LABELS: dict[Provider, str] = {
    Provider.SLACK: "Slack",
    Provider.TELEGRAM: "Telegram",
    Provider.EMAIL: "email",
    Provider.INTERACTIVE_SHELL: "the OpenSRE inbox",
}

REPORT_STYLE_GUIDE = """REPLY FORMAT (required; the scheduler renders it natively for each destination)
Write the reply as Markdown in exactly this shape:

1. Title line: `# <one emoji> <report name> · <weekday day month>`, e.g.
   `# ☀️ Engineering morning report · Mon 19 Oct`.
2. Headline: one sentence right under the title with the single most important
   takeaway and its number. Never a greeting or "Here is your report".
3. Two to five sections, most important first, each `## <one emoji> <name>`:
   🔴 Needs attention · ✅ Healthy · 📈 Metrics · 🔀 Pull requests · 🐛 Errors ·
   🚀 Deploys · 👥 Team · 📌 Notable. Put "Needs attention" first and only when
   something needs a person to act. Leave out a section with nothing to say;
   never write "None" or "No items".
4. Bullets: one fact each, at most about 20 words, starting with its subject in
   **bold**: `- **Main CI:** 79 runs, **0 failures**`.
   - Numbers in **bold**; a change gets its direction and baseline:
     `**12** failed runs ▲4 vs yesterday`. Never claim a trend without the baseline.
   - An action names who, what and where: `- **@davincios** — review
     [#6611 hourly cron](https://…) · waiting 13d`. With no owner, say **Unassigned**.
   - Many similar items become one bullet with a count and at most three
     examples: `- **17 PRs** have no reviewer: [#6780](…), [#6772](…), [#6770](…) and 14 more`.
5. Links: `[short label](url)` (an issue or PR number plus a few words); at most
   two per bullet; never a bare URL.
6. Times: relative where it reads better ("13d", "since Fri"), otherwise in the
   loop's time zone; never a full timestamp range in the body.
7. Gaps: when a source failed or is not connected and that changes what the
   reader can trust, end with one line `_Coverage: <what was not checked>_`.
   Never narrate method ("complete enumeration", "rechecked during preparation").
8. Nothing changed: the title, then one line `All quiet — <what was checked>.`
   and nothing else.

Never use tables, HTML, horizontal rules, headings below `##`, or code blocks
(inline `code` is fine). Never paste raw tool output. If the request below asks
for a different layout, follow its layout but keep these Markdown rules.

Example:
# ☀️ Engineering morning report · Mon 19 Oct
Main is green; **2 PRs** have waited **13 days** for review.

## 🔴 Needs attention
- **@davincios** — review [#6611 hourly cron](https://github.com/o/r/pull/6611) · waiting 13d
- **Unassigned** — **17 PRs** have no reviewer: [#6780](https://github.com/o/r/pull/6780), [#6772](https://github.com/o/r/pull/6772) and 15 more

## ✅ CI and deploys
- **Main CI:** 79 runs, **0 failures** (60 passed, 15 skipped, 4 cancelled)
- **Vercel:** Loops UI reached production at 00:17

## 🐛 Errors
- **Sentry:** no new or spiking issues across **4** projects
"""


def report_style_block(channels: Iterable[str], *, timezone: str = "") -> str:
    """The format guide plus where this reply goes and how long it may be."""
    providers = [_provider(value) for value in channels]
    destinations = [provider for provider in providers if provider is not None]
    if not destinations:
        destinations = [Provider.SLACK]
    names = ", ".join(_LABELS.get(provider, provider.value) for provider in destinations)
    budget = min(_LENGTH_BUDGET.get(provider, 3000) for provider in destinations)
    where = f"This reply is delivered to {names}. Keep it under about {budget} characters."
    if timezone.strip():
        where += f" The loop's time zone is {timezone.strip()}."
    return f"{REPORT_STYLE_GUIDE}\n{where}\n"


def _provider(value: str) -> Provider | None:
    try:
        return Provider(str(value).strip().lower())
    except ValueError:
        return None


__all__ = ["REPORT_STYLE_GUIDE", "report_style_block"]
