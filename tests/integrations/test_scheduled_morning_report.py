"""Tests for integration-owned scheduled delivering-morning-briefings data fetches."""

from __future__ import annotations

import pytest

from integrations import scheduled_skill_runner
from integrations.morning_report import fetch as morning_fetch

_BBC_RSS = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>BBC News</title>
    <item><title>First headline</title></item>
    <item><title>Second headline</title></item>
  </channel>
</rss>
"""


def test_fetch_headlines_skips_channel_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(morning_fetch, "_get", lambda _url: _BBC_RSS)
    assert morning_fetch.fetch_headlines() == ["First headline", "Second headline"]


@pytest.mark.parametrize("declaration", ["<!DOCTYPE rss>", "<!ENTITY x 'boom'>"])
def test_fetch_headlines_rejects_dtd_and_entities(
    monkeypatch: pytest.MonkeyPatch, declaration: str
) -> None:
    monkeypatch.setattr(morning_fetch, "_get", lambda _url: f"{declaration}<rss />")
    with pytest.raises(RuntimeError, match="forbidden DTD or entity"):
        morning_fetch.fetch_headlines()


def test_get_rejects_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == morning_fetch._MAX_RESPONSE_BYTES + 1
            return b"x" * limit

    monkeypatch.setattr(
        morning_fetch.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    with pytest.raises(RuntimeError, match="exceeded"):
        morning_fetch._get("https://example.invalid/feed")


def test_format_fetched_briefing_inputs_uses_city(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(morning_fetch, "fetch_weather", lambda city="": f"{city}: sunny")
    monkeypatch.setattr(morning_fetch, "fetch_headlines", lambda: ["One"])
    block = morning_fetch.format_fetched_briefing_inputs({"city": "Amsterdam"})
    assert "Amsterdam: sunny" in block
    assert "- One" in block


def test_prefetched_context_empty_for_other_skills() -> None:
    assert scheduled_skill_runner._prefetched_context("fixing-github-ci", {}) == ""


def test_prefetched_context_morning_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(morning_fetch, "fetch_weather", lambda _city="": "Paris: cloudy")
    monkeypatch.setattr(morning_fetch, "fetch_headlines", lambda: ["News"])
    block = scheduled_skill_runner._prefetched_context(
        "delivering-morning-briefings", {"city": "Paris"}
    )
    assert "Paris: cloudy" in block
    assert "- News" in block
