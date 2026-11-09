"""Compact human-facing summaries for ``github_cli`` tool results."""

from __future__ import annotations

import re
from typing import Any

from integrations.github.tools.github_cli.runner import positional_gh_tokens

_URL_RE = re.compile(r"https://github\.com/[^\s]+")
_ISSUE_OR_PR_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+)/(?P<kind>issues|pull)/(?P<number>\d+)"
)

_MUTATE_VERBS: dict[tuple[str, str], str] = {
    ("issue", "create"): "Created",
    ("issue", "close"): "Closed",
    ("issue", "reopen"): "Reopened",
    ("issue", "comment"): "Commented on",
    ("issue", "edit"): "Updated",
    ("pr", "create"): "Created",
    ("pr", "merge"): "Merged",
    ("pr", "close"): "Closed",
    ("pr", "reopen"): "Reopened",
    ("pr", "comment"): "Commented on",
    ("pr", "ready"): "Marked ready for review",
    ("pr", "edit"): "Updated",
    ("repo", "create"): "Created repository",
    ("repo", "fork"): "Forked repository",
}


def _first_url(text: str) -> str:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,);]") if match else ""


def _object_label(url: str) -> str:
    match = _ISSUE_OR_PR_RE.search(url)
    if not match:
        return ""
    kind = "PR" if match.group("kind") == "pull" else "issue"
    return f"{kind} #{match.group('number')}"


def _pr_number_label(rest: list[str]) -> str:
    """Return ``PR #<n>`` from merge args, ignoring flag values like ``-R owner/repo``.

    ``rest`` is positionals after the top-level command with bare ``-…`` flags
    removed (so ``["merge", "3996"]`` or ``["merge", "o/r", "3996"]``). The PR
    number is the first all-digit token after the subcommand — not ``rest[1]``,
    which may be a value that followed a stripped flag.
    """
    for token in rest[1:]:
        if token.isdigit():
            return f"PR #{token}"
    return ""


def summarize_gh_result(
    *,
    args: list[str],
    ok: bool,
    stdout: str = "",
    stderr: str = "",
    error: str = "",
    error_type: str = "",
) -> str:
    """Return a one-line (or short) user-facing summary for a ``gh`` result."""
    cleaned_args = [str(a) for a in args]
    out = (stdout or "").strip()
    err = (error or stderr or "").strip()

    if not ok:
        if error_type == "missing_binary":
            return "GitHub CLI (`gh`) is not installed or not on PATH."
        if error_type == "configuration_error":
            return "GitHub token is missing; connect or reconnect GitHub in the OpenSRE app."
        if error_type == "timeout":
            return err or "gh timed out."
        return f"GitHub action failed to run: {err or 'unknown error'}"

    positionals = positional_gh_tokens(cleaned_args)
    command = positionals[0].lower() if positionals else ""
    rest = [p for p in positionals[1:] if not p.startswith("-")]
    sub = rest[0].lower() if rest else ""
    url = _first_url(out)
    label = _object_label(url)
    verb = _MUTATE_VERBS.get((command, sub))

    if verb:
        if "--auto" in cleaned_args and command == "pr" and sub == "merge":
            verb = "Enabled auto-merge for"
            if not label:
                label = _pr_number_label(rest)
        if url and label:
            return f"{verb} {label}: {url}"
        if url:
            return f"{verb}: {url}"
        if label:
            return f"{verb} {label}."
        if out and "\n" not in out and len(out) < 160:
            return f"{verb}: {out}"
        return f"{verb}."

    if url and label:
        return f"{label}: {url}"
    if url:
        return url
    if out:
        # Raw JSON from ``gh api`` is for the model, not the transcript — a sliced
        # JSON string still reads as a dump. Keep a short prose acknowledgement.
        stripped = out.lstrip()
        if stripped[:1] in "{[" or out.count('":') >= 2:
            if command == "api":
                return "GitHub API call succeeded."
            return "GitHub command succeeded."
        lines = [line for line in out.splitlines() if line.strip()]
        if not lines:
            return "GitHub command succeeded."
        if len(lines) == 1 and len(lines[0]) < 200:
            return lines[0]
        preview = "; ".join(lines[:3])
        if len(lines) > 3:
            preview = f"{preview}; …and {len(lines) - 3} more"
        if len(preview) > 240:
            preview = preview[:237].rstrip() + "..."
        return preview
    return "GitHub command succeeded."


def attach_summary(payload: dict[str, Any], *, args: list[str]) -> dict[str, Any]:
    """Return *payload* with a ``summary`` field derived from the gh result."""
    enriched = dict(payload)
    enriched["summary"] = summarize_gh_result(
        args=args,
        ok=bool(payload.get("ok")),
        stdout=str(payload.get("stdout") or ""),
        stderr=str(payload.get("stderr") or ""),
        error=str(payload.get("error") or ""),
        error_type=str(payload.get("error_type") or ""),
    )
    return enriched


__all__ = ["attach_summary", "summarize_gh_result"]
