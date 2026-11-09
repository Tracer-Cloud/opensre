"""Gather Sentry issue context and turn it into a coding task for Pi.

Resolves the Sentry config, parses the issue URL, fetches the issue, and compacts
it into a short, **masked** task description. The output is fed (as untrusted text)
to the coding agent, which adds its own safety rules + prompt-injection guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

import httpx

from infrastructure.safety.masking import MaskingPolicy, MaskingRules
from integrations.sentry import SentryConfig, get_sentry_issue, sentry_config_from_env
from integrations.sentry.issue_url import parse_sentry_issue_url
from tools.cross_vendor.fix_sentry_issue.errors import (
    ERR_INVALID_INPUT,
    ERR_ISSUE_NOT_FOUND,
    ERR_SENTRY_UNAVAILABLE,
    FixIssueError,
)

_MAX_VALUE_CHARS = 500


@dataclass(frozen=True)
class IssueContext:
    """Resolved issue identity + the masked task description handed to Pi."""

    issue_id: str
    task: str


def _resolve_config() -> SentryConfig:
    config = sentry_config_from_env()
    if config is None:
        raise FixIssueError(
            ERR_SENTRY_UNAVAILABLE,
            "Sentry is not configured. Set SENTRY_ORG_SLUG and SENTRY_AUTH_TOKEN "
            "(and SENTRY_URL for self-hosted).",
        )
    return config


def _build_task(issue: dict) -> str:
    """Compact a Sentry issue dict into a short, masked coding task for Pi."""
    masker = MaskingRules(MaskingPolicy.from_env())

    def field(value: object, *, limit: int | None = None) -> str:
        text = str(value or "").strip()
        if limit:
            text = text[:limit]
        return masker.mask(text)

    raw_meta = issue.get("metadata")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    title = field(issue.get("title"))
    culprit = field(issue.get("culprit"))
    etype = field(meta.get("type"))
    evalue = field(meta.get("value"), limit=_MAX_VALUE_CHARS)
    filename = field(meta.get("filename"))
    function = field(meta.get("function"))
    level = field(issue.get("level"))
    count = field(issue.get("count"))

    error = f"{etype}: {evalue}" if etype and evalue else (etype or evalue)
    if filename and function:
        location = f"{filename} in {function}"
    else:
        location = filename or function

    lines = ["Fix the root cause of this Sentry issue in the current repository.", ""]
    if title:
        lines.append(f"Issue: {title}")
    if error:
        lines.append(f"Error: {error}")
    if culprit:
        lines.append(f"Culprit: {culprit}")
    if location:
        lines.append(f"Location: {location}")
    if level:
        lines.append(f"Level: {level}")
    if count:
        lines.append(f"Times seen: {count}")
    lines += ["", "Make a minimal, correct fix and explain what you changed and why."]
    return "\n".join(lines)


def _fetch_issue(config: SentryConfig, issue_id: str) -> dict:
    """Fetch the issue, mapping HTTP/network errors to a clean FixIssueError.

    ``get_sentry_issue`` calls ``raise_for_status()``, so a missing issue (404) or
    a bad token (401/403) raises ``httpx.HTTPStatusError`` — the most common
    real-world failures. Map them to ``error_kind`` rather than letting them escape
    as unexpected system errors.
    """
    try:
        issue = get_sentry_issue(config=config, issue_id=issue_id)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == HTTPStatus.NOT_FOUND:
            raise FixIssueError(
                ERR_ISSUE_NOT_FOUND, f"Sentry issue {issue_id} not found (404). Check the URL."
            ) from exc
        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise FixIssueError(
                ERR_SENTRY_UNAVAILABLE,
                f"Sentry rejected the request ({status}); check SENTRY_AUTH_TOKEN access.",
            ) from exc
        raise FixIssueError(
            ERR_SENTRY_UNAVAILABLE, f"Sentry request failed (HTTP {status})."
        ) from exc
    except httpx.RequestError as exc:
        raise FixIssueError(ERR_SENTRY_UNAVAILABLE, f"Could not reach Sentry: {exc}") from exc

    if not issue:
        raise FixIssueError(
            ERR_ISSUE_NOT_FOUND, f"Sentry issue {issue_id} not found (empty response)."
        )
    return issue


def gather_issue_context(sentry_url: str | None) -> IssueContext:
    """Parse the URL, fetch the issue, and build the masked task. Raises FixIssueError."""
    ref = parse_sentry_issue_url(sentry_url)
    if ref is None:
        raise FixIssueError(
            ERR_INVALID_INPUT,
            "Not a recognizable Sentry issue URL (expected .../issues/<id>/).",
        )

    config = _resolve_config()
    if ref.organization_slug:
        # The org in the issue URL is authoritative for *which* org to query; the
        # env token still authenticates. Falls back to the configured org otherwise.
        config = config.model_copy(update={"organization_slug": ref.organization_slug})
    issue = _fetch_issue(config, ref.issue_id)
    return IssueContext(issue_id=ref.issue_id, task=_build_task(issue))
