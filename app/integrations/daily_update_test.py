from __future__ import annotations

from datetime import UTC, date, datetime

from app.integrations.daily_update import (
    Contributor,
    PullRequestSummary,
    _github_repo_api_url,
    _name_looks_like_bot,
    build_daily_update,
    build_fallback_highlights,
    compute_daily_window,
    format_name_list,
    render_markdown,
    summarize_highlights,
)


def _pull_request(
    *,
    number: int = 101,
    title: str = "Add daily update workflow",
    author_display_name: str = "Alice",
    contributors: tuple[Contributor, ...] | None = None,
) -> PullRequestSummary:
    return PullRequestSummary(
        number=number,
        title=title,
        url=f"https://github.com/Tracer-Cloud/opensre/pull/{number}",
        author_login=author_display_name.lower(),
        author_display_name=author_display_name,
        merged_at=datetime(2026, 4, 2, 18, 30, tzinfo=UTC),
        body="This adds the workflow and supporting automation.",
        labels=("automation",),
        changed_files=("app/integrations/daily_update.py", ".github/workflows/daily-update.yml"),
        additions=120,
        deletions=10,
        contributors=contributors or (Contributor(login=author_display_name.lower(), display_name=author_display_name),),
    )


def test_compute_daily_window_handles_gmt() -> None:
    window = compute_daily_window(now=datetime(2026, 1, 15, 23, 59, tzinfo=UTC))

    assert window.london_date == date(2026, 1, 15)
    assert window.start_utc == datetime(2026, 1, 15, 0, 0, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 1, 16, 0, 0, tzinfo=UTC)


def test_compute_daily_window_handles_bst() -> None:
    window = compute_daily_window(now=datetime(2026, 6, 15, 22, 59, tzinfo=UTC))

    assert window.london_date == date(2026, 6, 15)
    assert window.start_utc == datetime(2026, 6, 14, 23, 0, tzinfo=UTC)
    assert window.end_utc == datetime(2026, 6, 15, 23, 0, tzinfo=UTC)


def test_format_name_list_uses_oxford_comma() -> None:
    rendered = format_name_list(["vaibhav upreti", "paul", "Rohit Rajan"])

    assert rendered == "vaibhav upreti, paul, and Rohit Rajan"


def test_name_looks_like_bot_filters_action_accounts() -> None:
    assert _name_looks_like_bot("contrib-readme-action")
    assert _name_looks_like_bot("GitHub Actions")
    assert not _name_looks_like_bot("Tan Wee Joe")


def test_github_repo_api_url_keeps_owner_repo_segments() -> None:
    url = _github_repo_api_url("Tracer-Cloud/opensre", "pulls?state=closed")

    assert url == "https://api.github.com/repos/Tracer-Cloud/opensre/pulls?state=closed"


def test_build_fallback_highlights_uses_titles() -> None:
    highlights = build_fallback_highlights((
        _pull_request(number=1, title="Flatten app package"),
        _pull_request(number=2, title="Add more synthetic tests"),
    ))

    assert highlights == (
        "Flatten app package.",
        "Add more synthetic tests.",
    )


def test_summarize_highlights_falls_back_when_llm_fails(monkeypatch) -> None:
    pull_requests = (_pull_request(title="Add more synthetic tests"),)

    def _raise() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.integrations.daily_update.get_llm_for_reasoning", _raise)

    highlights, fallback_used = summarize_highlights(
        "Tracer-Cloud/opensre",
        compute_daily_window(london_date=date(2026, 4, 2)),
        pull_requests,
    )

    assert fallback_used is True
    assert highlights == ("Add more synthetic tests.",)


def test_render_outputs_include_expected_sections() -> None:
    update = build_daily_update(
        "Tracer-Cloud/opensre",
        compute_daily_window(london_date=date(2026, 4, 2)),
        (
            _pull_request(
                contributors=(
                    Contributor(login="alice", display_name="Alice"),
                    Contributor(login="bob", display_name="Bob"),
                )
            ),
        ),
    )

    markdown = render_markdown(update)

    assert "# Daily opensre update" in markdown
    assert "Thanks to everyone who contributed today: Alice and Bob :pray::rocket:" in markdown
    assert "## Main updates shipped" in markdown
    assert "## Source pull requests" in markdown
    assert "[#101](https://github.com/Tracer-Cloud/opensre/pull/101)" in markdown


def test_build_daily_update_uses_empty_day_fallback() -> None:
    update = build_daily_update(
        "Tracer-Cloud/opensre",
        compute_daily_window(london_date=date(2026, 4, 2)),
        (),
    )

    assert update.fallback_used is True
    assert update.highlights == ("No pull requests were merged into `main` today.",)
    assert "no human contributors recorded in merged PRs today" in update.thanks_line
