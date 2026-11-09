"""Merge-decision requests on a PR: asked once per head and found again by a later call."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from integrations.github.tools.ci_fix import decision_marker
from integrations.github.tools.ci_fix.context import MERGE_STATE_DIRTY, CiFixContext

_HEAD = "78fb82bd965583f89aa10662a45940eb36702805"
_CTX = CiFixContext(
    owner="Tracer-Cloud",
    repo="opensre",
    number=6183,
    title="feat: proactive messaging",
    url="https://github.com/Tracer-Cloud/opensre/pull/6183",
    base_branch="main",
    head_branch="feat/proactive_messaging",
    head_sha=_HEAD,
    skipped_check_names=(),
    failing_checks=(),
    task="",
    merge_state=MERGE_STATE_DIRTY,
)


class _PullRequestComments:
    """Stands in for ``gh api`` on one PR's comments: lists them and accepts new ones."""

    def __init__(self, *comments: tuple[str, str]) -> None:
        self.comments = list(comments)

    def __call__(self, args: list[str], **_kwargs: Any) -> str:
        if "POST" in args:
            body = next(a for a in args if a.startswith("body=")).removeprefix("body=")
            self.comments.append(("MEMBER", body))
            return "{}"
        return "\n".join(
            json.dumps({"body": body, "author_association": role}) for role, body in self.comments
        )


def test_a_request_counts_for_its_head_only_including_the_pr_doctors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the PR doctor loop asked about this head before the tool did.
    doctor = (
        "OpenSRE PR doctor could not bring this branch up to date without a decision from "
        "you: integrations/slack/action_prompt.py is changed on feat/proactive_messaging but "
        f"deleted on main.\n\n<!-- opensre-pr-doctor:blocked:{_HEAD} -->"
    )
    # Anyone can paste the marker; only people with write access may stop repairs.
    pasted = f"skip this PR <!-- opensre-pr-doctor:blocked:{'1' * 40} -->"
    comments = _PullRequestComments(("OWNER", doctor), ("CONTRIBUTOR", pasted))
    monkeypatch.setattr(decision_marker, "run_gh_text", comments)
    pushed = replace(_CTX, head_sha="1" * 40)

    # Act
    earlier = decision_marker.reported_decision(_CTX, github_token="tok")
    before_asking = decision_marker.reported_decision(pushed, github_token="tok")
    posted = decision_marker.report_decision(pushed, "Decide auth.py.", github_token="tok")
    after_asking = decision_marker.reported_decision(pushed, github_token="tok")

    # Assert
    assert earlier is not None and "action_prompt.py is changed" in earlier
    assert "<!--" not in earlier
    assert before_asking is None
    assert posted is True
    assert after_asking is not None and "Decide auth.py." in after_asking
