"""Shared data-blob detection under the two caller policies.

The tool-result hider (``display_text.is_data_blob``, also used by action
rendering for ``↳`` child previews) and the reply-prose collapse (streaming
renderer ``_looks_like_raw_dump``) run this one mechanism with different
thresholds. These tests pin each policy and lock in that they differ on
purpose, so the two sites can never silently drift apart again.
"""

from __future__ import annotations

from infrastructure.text import looks_like_data_blob

# The two live policies, named here so a threshold change is a deliberate edit.
_TOOL_PAYLOAD = {"min_json_keys": 2, "honor_open_bracket": True}
_REPLY_PARAGRAPH = {
    "min_chars": 200,
    "min_json_keys": 3,
    "structural_ratio": 0.15,
    "truncation_marker": "output truncated",
}


def test_tool_policy_hides_a_short_mid_object_fragment() -> None:
    # Arrange: a capped gh-api response cut mid-value — two keys, well under 200 chars.
    fragment = 'foo","followers_url":"https://api.github.com/u","gists_url":"https://x"'

    # Act / Assert: the aggressive tool policy treats it as data...
    assert looks_like_data_blob(fragment, **_TOOL_PAYLOAD) is True
    # ...while the conservative reply policy leaves it as prose (below its floor).
    assert looks_like_data_blob(fragment, **_REPLY_PARAGRAPH) is False


def test_tool_policy_honors_an_opening_bracket() -> None:
    assert looks_like_data_blob('{"a": 1}', **_TOOL_PAYLOAD) is True
    # The reply policy ignores shape and needs bulk before it collapses.
    assert looks_like_data_blob('{"a": 1}', **_REPLY_PARAGRAPH) is False


def test_reply_policy_collapses_a_large_dense_blob() -> None:
    blob = "\n".join(f'"field_{i}": "value_{i}",' for i in range(40))
    assert looks_like_data_blob(blob, **_REPLY_PARAGRAPH) is True
    assert looks_like_data_blob(blob, **_TOOL_PAYLOAD) is True


def test_reply_policy_keeps_prose_that_merely_mentions_a_colon() -> None:
    prose = (
        "The deploy finished and the health check passed. One note: the cache "
        "warm-up ran twice, which is expected on a cold start after a release. "
        "Nothing else looked off across the three services I checked just now."
    )
    assert looks_like_data_blob(prose, **_REPLY_PARAGRAPH) is False


def test_reply_policy_collapses_on_the_truncation_marker() -> None:
    text = "x" * 300 + " output truncated"
    assert looks_like_data_blob(text, **_REPLY_PARAGRAPH) is True


def test_empty_text_is_never_a_blob() -> None:
    assert looks_like_data_blob("   ", **_TOOL_PAYLOAD) is False
    assert looks_like_data_blob("", **_REPLY_PARAGRAPH) is False
