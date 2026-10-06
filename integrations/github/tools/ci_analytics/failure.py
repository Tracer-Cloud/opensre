"""How a CI analysis GitHub could not serve reads to the user, the model and the turn host."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from config.constants.github import GITHUB_INTEGRATION_SETUP_CLI
from core.tool_framework.utils import tool_unavailable
from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
from infrastructure.scheduling.scheduler.types import TaskReport
from integrations.github.client import GitHubApiError, GitHubFailureKind, github_failure_kind

_SOURCE = "github"
# A rate limit lifting sooner than this is named in seconds, not as a clock time.
_SHORT_WAIT_SECONDS = 90

# Failures that describe GitHub, the network or the token rather than a fault
# in this code.
_OPERATIONAL_FAILURES = frozenset(
    {
        GitHubFailureKind.RATE_LIMITED,
        GitHubFailureKind.UNAUTHORIZED,
        GitHubFailureKind.NOT_FOUND,
        GitHubFailureKind.TLS_UNTRUSTED,
        GitHubFailureKind.UNREACHABLE,
        GitHubFailureKind.SERVER_ERROR,
    }
)

# What the model does next. Every failure has already been retried as far as
# retrying helps (the client resends transient failures and waits out short
# rate limits), so none of them asks for another call in the same turn.
_NEXT_STEP = {
    GitHubFailureKind.RATE_LIMITED: (
        "Do not run the analysis again before the limit lifts: tell the user when it "
        "lifts and offer to run it then."
    ),
    GitHubFailureKind.UNAUTHORIZED: (
        "The same token fails the same way: hand the user the setup command instead of retrying."
    ),
    GitHubFailureKind.NOT_FOUND: (
        "The same owner/repo fails the same way: confirm the repository with the user."
    ),
    # Not "stop": the skill that ran the analysis may go on without GitHub.
    GitHubFailureKind.TLS_UNTRUSTED: (
        "Retrying cannot help until the certificate problem is fixed: report it in one line, "
        "then continue with the next step instead of retrying."
    ),
    GitHubFailureKind.UNREACHABLE: (
        "The read was already retried: report the blocker and offer the user a retry later."
    ),
    GitHubFailureKind.SERVER_ERROR: (
        "The read was already retried: report the blocker and offer the user a retry later."
    ),
}
_DEFAULT_NEXT_STEP = "A retry reads the same answer: report the blocker instead."


def is_operational_failure(exc: Exception) -> bool:
    """Whether ``exc`` is a fact about GitHub, the network or the token, not a fault in this code.

    The result already tells the user about such a failure, so it is logged
    as a warning without a stack instead of printed into the shell as an error.
    """
    return github_failure_kind(exc) in _OPERATIONAL_FAILURES


def _lifts(retry_after: float | None, now: datetime) -> str:
    if retry_after is None:
        return "right now"
    if retry_after < _SHORT_WAIT_SECONDS:
        return f"for about {math.ceil(retry_after)} more seconds"
    at = now + timedelta(seconds=retry_after)
    return f"until about {at:%H:%M} UTC (in {math.ceil(retry_after / 60)} minutes)"


def analysis_failure_line(exc: Exception, *, repository: str, now: datetime) -> str:
    """One line for the user: what blocked the read and what fixes it, never exception detail.

    ``now`` is the UTC time of the failure; a rate limit names when it lifts.
    """
    kind = github_failure_kind(exc)
    if kind is GitHubFailureKind.RATE_LIMITED:
        retry_after = exc.retry_after_seconds if isinstance(exc, GitHubApiError) else None
        return (
            f"GitHub is rate-limiting this token, so the Actions history of {repository} "
            f"can't be read {_lifts(retry_after, now)}. The token and the repository are fine."
        )
    if kind is GitHubFailureKind.UNAUTHORIZED:
        return (
            f"GitHub rejected the token for {repository}; it needs read access to Actions and "
            f"pull requests. Run `{GITHUB_INTEGRATION_SETUP_CLI}` and try again."
        )
    if kind is GitHubFailureKind.NOT_FOUND:
        return f"GitHub repository {repository} was not found, or this token can't see it."
    if kind is GitHubFailureKind.TLS_UNTRUSTED:
        return (
            f"OpenSRE couldn't verify GitHub's TLS certificate on this machine, so it can't "
            f"read {repository}. Run `opensre update`; if your network inspects TLS traffic, "
            "set SSL_CERT_FILE to its CA bundle and restart OpenSRE."
        )
    if kind is GitHubFailureKind.UNREACHABLE:
        return (
            f"GitHub stopped answering while OpenSRE read the Actions history of {repository}, "
            "even after retries. Check the network connection and try again."
        )
    if kind is GitHubFailureKind.SERVER_ERROR:
        return (
            f"GitHub kept returning server errors while OpenSRE read {repository}; GitHub may "
            "be degraded. Try again in a few minutes."
        )
    if kind is GitHubFailureKind.INVALID_RESPONSE:
        return f"GitHub returned an unexpected payload for {repository}; the report was not built."
    status = exc.status_code if isinstance(exc, GitHubApiError) else None
    answered = f"; GitHub answered HTTP {status}" if status is not None else ""
    return f"Could not read the GitHub Actions history of {repository}{answered}."


def analysis_failure(exc: Exception, *, owner: str, repo: str, now: datetime) -> dict[str, Any]:
    """The ``tool_unavailable`` envelope for an analysis GitHub could not serve.

    ``response_text`` is the user's line and ``error`` adds the model's next
    step. ``work_outcome`` marks the read as blocked, a finished answer, so the
    turn reports it instead of retrying a read that fails the same way. It
    stays retryable for the scheduler: a later run may find the limit lifted or
    the network back.
    """
    kind = github_failure_kind(exc)
    line = analysis_failure_line(exc, repository=f"{owner}/{repo}", now=now)
    retry_after = exc.retry_after_seconds if isinstance(exc, GitHubApiError) else None
    timing = {"retry_after_seconds": math.ceil(retry_after)} if retry_after is not None else {}
    outcome = WorkOutcome(
        status=WorkStatus.BLOCKED,
        error_kind=kind.value,
        operation=f"ci-analytics:{owner.casefold()}/{repo.casefold()}",
        evidence={"owner": owner, "repo": repo, **timing},
        retryable=True,
    )
    return tool_unavailable(
        _SOURCE,
        f"{line} {_NEXT_STEP.get(kind, _DEFAULT_NEXT_STEP)}",
        response_text=line,
        error_kind=kind.value,
        work_outcome=outcome.model_dump(mode="json"),
        **timing,
    )


def analysis_failure_report(exc: Exception, *, owner: str, repo: str, now: datetime) -> TaskReport:
    """A scheduled run's report when GitHub could not be read: the user's line, not a crash.

    Delivered like any report, so the loop's channels learn what blocked the
    read and when a rate limit lifts, instead of a failure only the logs
    explain. An operational failure is blocked, anything else failed; both
    stay retryable, so the schedule keeps its next tick.
    """
    line = analysis_failure_line(exc, repository=f"{owner}/{repo}", now=now)
    status = WorkStatus.BLOCKED if is_operational_failure(exc) else WorkStatus.FAILED
    return TaskReport(
        line, summary=line, work_status=status, error_kind=github_failure_kind(exc).value
    )


__all__ = [
    "analysis_failure",
    "analysis_failure_line",
    "analysis_failure_report",
    "is_operational_failure",
]
