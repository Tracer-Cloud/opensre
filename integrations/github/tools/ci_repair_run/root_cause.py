"""GitHub links and a root cause analysis for one finished CI repair demo.

Every statement comes from evidence the demo holds: the seed result, the pull
request read, and the repair run's record and attempt records. Claims are tied
to the commits the run itself saw: the seeded fault is named only when this
call committed it and the run first saw that commit failing, and a fix is
reported only when the pull request head is the commit the run verified. URLs
are written out in full because a terminal shows Markdown link text without
its URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from integrations.github.tools.ci_repair_demo.seed import SEEDED_FAULT
from integrations.github.tools.ci_repair_loop.storage import RepairStore

_OUTCOME_SUCCESS = "success"
_SHORT_SHA = 7
_SUMMARY_MAX_CHARS = 240
_DIFF_MAX_LINES = 20
_DIFF_HEADERS = ("diff --git ", "index ")
#: Two of these make the gateway hide tool text as a data blob; recorded text never adds one.
_KEY_SEPARATOR = '":'
_FENCE = "```"

_LINK_LABELS = (
    ("pull_request", "Pull request"),
    ("failing_commit", "Failing commit"),
    ("failed_run", "Failed run"),
    ("fix_commit", "Fix commit"),
    ("passing_run", "Passing run"),
)
_ANALYSIS_LABELS = (
    ("failure", "What failed"),
    ("cause", "Root cause"),
    ("fix", "Fix"),
    ("verification", "Verification"),
)


@dataclass(frozen=True)
class RepairEvidence:
    """What one repair run recorded; empty when the store could not be read."""

    #: The head the run first saw failing, and the repair commit it verified.
    initial_sha: str = ""
    fixed_sha: str = ""
    failing_checks: tuple[str, ...] = ()
    #: Every head the run saw was the head it was scheduled at or one it pushed itself.
    on_seeded_fixture: bool = False
    reason: str = ""
    fix_files: tuple[str, ...] = ()
    fix_summary: str = ""
    fix_diff: str = ""


def read_repair_evidence(task_id: str, store: RepairStore | None = None) -> RepairEvidence:
    """The run's first failure and the attempt that pushed the commit it verified."""
    try:
        repairs = store or RepairStore()
        run = repairs.get(task_id)
    except (OSError, ValueError):
        return RepairEvidence()
    attempts = [repairs.read_attempt(task_id, number) for number in range(1, run.attempts + 1)]
    first = attempts[0] if attempts else {}
    fix = next(
        (
            record
            for record in reversed(attempts)
            if run.fixed_sha and record.get("fix_head_sha") == run.fixed_sha
        ),
        {},
    )
    return RepairEvidence(
        initial_sha=run.initial_sha,
        fixed_sha=run.fixed_sha,
        failing_checks=_names(first.get("failing_checks")),
        on_seeded_fixture=run.fast_checks,
        reason=_plain(run.reason),
        fix_files=_names(fix.get("changed_files")),
        fix_summary=_first_sentence(fix.get("summary")),
        fix_diff=_short_diff(fix),
    )


def repair_verified(outcome: str, fix_commit: str, evidence: RepairEvidence) -> bool:
    """True when the repair succeeded and the pull request head is the commit it verified.

    A head someone pushed after the repair passed CI is never reported as its fix.
    """
    return outcome == _OUTCOME_SUCCESS and bool(fix_commit) and fix_commit == evidence.fixed_sha


def github_links(
    *,
    owner: str,
    repo: str,
    pr_number: int,
    pr_url: str,
    failing_commit: str,
    failed_run_id: int,
    fix_commit: str,
    passing_run_id: int,
    verified: bool,
) -> dict[str, str]:
    """Full URLs for the evidence the demo has; a missing id leaves its link out.

    The fix commit and passing run are linked only for a ``verified`` repair, so
    an unverified head is never labelled a fix.
    """
    base = f"https://github.com/{owner}/{repo}"
    links = {
        "pull_request": pr_url or f"{base}/pull/{pr_number}",
        "failing_commit": f"{base}/commit/{failing_commit}" if failing_commit else "",
        "failed_run": f"{base}/actions/runs/{failed_run_id}" if failed_run_id else "",
    }
    if verified:
        links["fix_commit"] = f"{base}/commit/{fix_commit}" if fix_commit else ""
        links["passing_run"] = f"{base}/actions/runs/{passing_run_id}" if passing_run_id else ""
    return {key: url for key, url in links.items() if url}


def root_cause_analysis(
    *,
    pr_number: int,
    outcome: str,
    failing_commit: str,
    fix_commit: str,
    seeded_here: bool,
    evidence: RepairEvidence,
) -> dict[str, str]:
    """What failed, why, what fixed it, and how that was verified.

    ``seeded_here`` is true when this call committed the failing change. The
    run's check names and the seeded fault describe ``failing_commit`` only when
    the run first saw that commit failing, so a head that moved before the repair
    was scheduled is never blamed on the seed.
    """
    failing = failing_commit[:_SHORT_SHA]
    observed = bool(failing) and evidence.initial_sha == failing_commit
    head = f"commit {failing}" if failing else "the head"
    checks = _checks(evidence.failing_checks if observed else ())
    analysis = {"failure": f"{checks} failed on {head} of PR #{pr_number}."}
    if seeded_here and observed and evidence.on_seeded_fixture:
        analysis["cause"] = f"Commit {failing} {SEEDED_FAULT}"
    if not repair_verified(outcome, fix_commit, evidence):
        analysis["fix"] = _unverified_fix(outcome, fix_commit, evidence)
        return analysis
    fix = fix_commit[:_SHORT_SHA]
    files = ", ".join(f"`{name}`" for name in evidence.fix_files)
    analysis["fix"] = f"Commit {fix} changed {files}." if files else f"Commit {fix} is the repair."
    if evidence.fix_summary:
        analysis["fix_summary"] = evidence.fix_summary
    if evidence.fix_diff:
        analysis["fix_diff"] = evidence.fix_diff
    analysis["verification"] = f"The pull request's checks passed on commit {fix}."
    return analysis


def render_analysis(links: dict[str, str], analysis: dict[str, str]) -> str:
    """The links and the analysis as Markdown, every URL written out in full."""
    lines = ["**GitHub links**"]
    lines.extend(f"- {label}: {links[key]}" for key, label in _LINK_LABELS if key in links)
    lines.extend(["", "**Root cause analysis**"])
    for key, label in _ANALYSIS_LABELS:
        text = analysis.get(key)
        if not text:
            continue
        if key == "fix" and analysis.get("fix_summary"):
            text = f"{text} The coding agent reported: {analysis['fix_summary']}"
        lines.append(f"- {label}: {text}")
    diff = analysis.get("fix_diff")
    if diff:
        lines.extend(["", f"{_FENCE}diff", diff, _FENCE])
    return "\n".join(lines)


def _unverified_fix(outcome: str, fix_commit: str, evidence: RepairEvidence) -> str:
    """Why no fix is reported: the repair did not succeed, or the head is not its commit."""
    if outcome != _OUTCOME_SUCCESS or not fix_commit:
        return f"No verified fix: {evidence.reason}" if evidence.reason else "No verified fix."
    if evidence.fixed_sha:
        return (
            f"Not attributed: the repair verified commit {evidence.fixed_sha[:_SHORT_SHA]}, "
            f"but the pull request head is now {fix_commit[:_SHORT_SHA]}."
        )
    return "Not attributed: the repair record could not be read."


def _plain(value: object) -> str:
    """One line of recorded text with no ``"`` left to form a data-blob key separator."""
    return " ".join(str(value or "").split()).replace('"', "'")


def _names(value: object) -> tuple[str, ...]:
    """Check or file names as plain text; a quote or backtick in one cannot break the report."""
    if not isinstance(value, list):
        return ()
    names = (_plain(item).replace("`", "'") for item in value)
    return tuple(name for name in names if name)


def _checks(names: tuple[str, ...]) -> str:
    if not names:
        return "The pull request's checks"
    listed = ", ".join(f"`{name}`" for name in names)
    return f"Check {listed}" if len(names) == 1 else f"Checks {listed}"


def _first_sentence(value: object) -> str:
    """The summary's opening sentence on one line, with no data-blob key separator."""
    text = _plain(str(value or "").strip().split("\n\n", 1)[0])
    sentence, separator, _rest = text.partition(". ")
    if separator:
        text = f"{sentence}."
    if len(text) > _SUMMARY_MAX_CHARS:
        text = text[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return text


def _short_diff(record: dict[str, Any]) -> str:
    """The attempt's diff without git headers; empty when cut, long, or unsafe to show."""
    diff = record.get("diff")
    if not isinstance(diff, str) or record.get("diff_truncated"):
        return ""
    if _KEY_SEPARATOR in diff or _FENCE in diff:
        return ""
    lines = [line for line in diff.strip("\n").splitlines() if not line.startswith(_DIFF_HEADERS)]
    if not lines or len(lines) > _DIFF_MAX_LINES:
        return ""
    return "\n".join(lines)
