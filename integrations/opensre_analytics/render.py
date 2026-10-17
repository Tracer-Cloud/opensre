"""Render a daily growth snapshot as a chat-ready morning report.

Output is Slack mrkdwn (``*bold*``, ``_italic_``) with plain URLs: scheduled
delivery posts the text as-is and strips anything shaped like an HTML tag,
which would also remove Slack's ``<url|label>`` links.
"""

from __future__ import annotations

from datetime import date, timedelta

from integrations.opensre_analytics.growth import (
    BASELINE_DAYS,
    USERS_PAGE_LIMIT,
    DailyCount,
    GrowthSnapshot,
)

_SECTION_NAMES = {
    "accounts": "accounts and prompts",
    "installs": "installs",
    "overview": "product overview",
}


def _day_label(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"


def _change(count: DailyCount, previous_label: str) -> str:
    parts: list[str] = []
    if count.previous is not None:
        delta = count.value - count.previous
        if delta == 0:
            parts.append(f"flat vs {previous_label}")
        else:
            arrow = "▲" if delta > 0 else "▼"
            percent = f" ({delta / count.previous:+.0%})" if count.previous else ""
            parts.append(f"{arrow} {abs(delta):,}{percent} vs {previous_label}")
    if count.baseline_avg is not None:
        parts.append(f"{BASELINE_DAYS}-day avg {count.baseline_avg:,.1f}")
    return f"  {' · '.join(parts)}" if parts else ""


def _line(label: str, count: DailyCount, previous_label: str) -> str:
    return f"• {label}: *{count.value:,}*{_change(count, previous_label)}"


def headline(snapshot: GrowthSnapshot) -> str:
    """One-line summary for run history and notifications."""
    parts: list[str] = []
    accounts = snapshot.accounts
    if accounts is not None and accounts.new_accounts is not None:
        parts.append(f"{accounts.new_accounts.value:,} new accounts")
    if accounts is not None:
        parts.append(f"{accounts.prompts.value:,} prompts")
    if snapshot.installs is not None:
        parts.append(f"{snapshot.installs.new_installs.value:,} installs")
    figures = " · ".join(parts) if parts else "no figures available"
    return f"OpenSRE growth {snapshot.day.isoformat()}: {figures}"


def render_growth_report(snapshot: GrowthSnapshot) -> str:
    """The morning report for one snapshot."""
    previous = f"{snapshot.day - timedelta(days=1):%a}"
    lines = [f"🌅 *OpenSRE daily growth · {_day_label(snapshot.day)}*"]
    lines.append("_UTC day · OpenSRE team accounts excluded from account figures_")

    accounts = snapshot.accounts
    if accounts is not None:
        lines += ["", "*👥 Accounts*"]
        if accounts.new_accounts is not None:
            lines.append(_line("New accounts", accounts.new_accounts, previous))
        lines.append(_line("Active accounts", accounts.active_accounts, previous))
        lines.append(f"• Total accounts: *{accounts.total_accounts:,}*")

    overview = snapshot.overview
    if accounts is not None or overview is not None:
        lines += ["", "*💬 Usage*"]
        if accounts is not None:
            lines.append(_line("Prompts", accounts.prompts, previous))
            lines.append(_line("Sessions", accounts.sessions, previous))
            lines.append(_line("Tool calls", accounts.tool_calls, previous))
        if overview is not None:
            lines.append(_line("Hosted LLM calls", overview.hosted_llm_calls, previous))
            lines.append(
                _line("Daily active users (all installs)", overview.daily_active_users, previous)
            )

    installs = snapshot.installs
    if installs is not None:
        lines += ["", "*📦 Installs*"]
        lines.append(_line("New installations", installs.new_installs, previous))
        lines.append(_line("Linked an account", installs.account_linked, previous))
        lines.append(_line("Activated", installs.activated, previous))
        if installs.llm_callers is not None:
            lines.append(_line("Installations calling the LLM", installs.llm_callers, previous))

    if accounts is not None and accounts.top_accounts:
        lines += ["", "*🏆 Most prompts*"]
        lines += [
            f"{rank}. {account.name} · {account.prompts:,}"
            for rank, account in enumerate(accounts.top_accounts, start=1)
        ]

    notes: list[str] = []
    if snapshot.unavailable:
        missing = ", ".join(
            f"{_SECTION_NAMES.get(name, name)} ({reason})"
            for name, reason in snapshot.unavailable.items()
        )
        notes.append(f"⚠️ Unavailable today: {missing}.")
    if accounts is not None and accounts.partial:
        notes.append(
            f"_Account usage reached the {USERS_PAGE_LIMIT:,}-account page limit; sums may be low._"
        )
    if snapshot.dashboard_url:
        notes.append(f"Dashboard: {snapshot.dashboard_url}")
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)


__all__ = ["headline", "render_growth_report"]
