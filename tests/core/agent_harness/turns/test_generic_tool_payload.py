"""The generic tool formatter must not flood the transcript with raw JSON.

A tool result with no user-facing field (response_text, summary, stdout, error)
is for the model only — the tool invocation is already shown at tool_start, so
dumping its raw JSON payload would flood the execution transcript. Genuine text
output and real summaries are still shown.
"""

from __future__ import annotations

import json

from core.agent_harness.turns.display_text import format_generic_tool_payload
from core.llm.types import ToolCall


def _result(content: object):
    class _R:
        pass

    r = _R()
    r.content = content if isinstance(content, str) else json.dumps(content)
    r.details = content if isinstance(content, dict) else None
    r.is_error = False
    return r


def _call(name: str) -> ToolCall:
    return ToolCall(id="t1", name=name, input={"owner": "acme", "repo": "svc"})


def test_opaque_json_object_is_hidden() -> None:
    # A raw JSON payload with no user-facing field (e.g. a gh-api response) is
    # for the model, not the transcript — hide it; the reply summarizes it.
    payload = {"ok": True, "available": True, "repository": {"full_name": "acme/svc"}}
    assert format_generic_tool_payload(_call("get_github_repository"), _result(payload)) == ""


def test_json_summary_preview_is_hidden() -> None:
    """gh used to put a sliced JSON string in ``summary`` — that must not paint."""
    sliced = (
        '{"login": "Tracer-Cloud", "followers_url": '
        '"https://api.github.com/users/Tracer-Cloud/followers", '
        '"gists_url": "https://api.github.com/users/Tr...'
    )
    payload = {"ok": True, "stdout": sliced, "summary": sliced}
    assert format_generic_tool_payload(_call("github_cli"), _result(payload)) == ""


def test_prose_summary_is_still_shown() -> None:
    payload = {
        "ok": True,
        "stdout": '{"id": 1}',
        "summary": "GitHub API call succeeded.",
    }
    assert (
        format_generic_tool_payload(_call("github_cli"), _result(payload))
        == "GitHub API call succeeded."
    )


def test_opaque_json_list_is_hidden() -> None:
    assert format_generic_tool_payload(_call("list_runs"), _result([{"id": 1}, {"id": 2}])) == ""


def test_github_repository_summary_is_shown_not_blob() -> None:
    payload = {
        "available": True,
        "summary": "Tracer-Cloud/opensre · 42★ · main · public · Python",
        "repository": {"full_name": "Tracer-Cloud/opensre", "stargazers_count": 42},
    }
    shown = format_generic_tool_payload(_call("get_github_repository"), _result(payload))
    assert shown == "Tracer-Cloud/opensre · 42★ · main · public · Python"


def test_grafana_metrics_summary_is_shown_not_blob() -> None:
    payload = {
        "available": True,
        "summary": "3 series for `http_requests_total` for api",
        "metrics": [{"metric": {}, "values": []}],
    }
    shown = format_generic_tool_payload(_call("query_grafana_metrics"), _result(payload))
    assert shown == "3 series for `http_requests_total` for api"


def test_truncated_json_blob_is_hidden_not_dumped_raw() -> None:
    # A capped gh-api response arrives as invalid (cut-off) JSON — it must not
    # slip past the JSON check and dump raw. Detection is by shape, not parse.
    trunc = '{"full_name":"Tracer-Cloud/opensre","followers_url":"https://api.github.com/users/Tr'
    assert (
        format_generic_tool_payload(_call("github_cli"), _result({"ok": True, "stdout": trunc}))
        == ""
    )
    assert format_generic_tool_payload(_call("github_cli"), _result(trunc)) == ""
    # A mid-object fragment (starts on a value, not ``{``) is still a data blob:
    # key-density detection catches it where a first-char check would not.
    frag = (
        'https://github.com/x","followers_url":"https://api.github.com/u",'
        '"following_url":"https://api.github.com/f","gists_url":"https://api.github.com/g"'
    )
    assert (
        format_generic_tool_payload(_call("github_cli"), _result({"ok": True, "stdout": frag}))
        == ""
    )


def test_a_real_summary_is_still_shown() -> None:
    payload = {"ok": True, "summary": "3 workflows, 12 runs"}
    shown = format_generic_tool_payload(_call("list_runs"), _result(payload))
    assert shown == "3 workflows, 12 runs"


def test_plain_text_output_is_still_shown() -> None:
    shown = format_generic_tool_payload(_call("shell"), _result("build succeeded"))
    assert "build succeeded" in shown


def test_json_stdout_is_hidden_and_plain_stdout_is_shown() -> None:
    # gh-api JSON stdout is data the reply already summarizes — hide it rather
    # than dump a wall unattached to the command. Plain-text output (ls, git) is
    # the user's real result and shows as-is.
    json_stdout = _result({"ok": True, "stdout": '{"id": 18260225, "name": "main"}'})
    assert format_generic_tool_payload(_call("github_cli"), json_stdout) == ""

    plain_stdout = _result({"ok": True, "stdout": "total 56\ndrwxr-xr-x  15 user"})
    assert "drwxr-xr-x" in format_generic_tool_payload(_call("shell"), plain_stdout)


def test_verbose_output_is_capped_for_display_only() -> None:
    from core.agent_harness.turns.display_text import cap_for_display

    big = "\n".join(f"run {i} failure 2026-08-01T09:11:00Z" for i in range(50))
    capped = cap_for_display(big)

    # Display is a bounded head + one Droid-style peek marker; the full text is untouched.
    assert capped.count("\n") + 1 <= 13
    assert "… 38 more, Ctrl+O to view" in capped
    assert "output truncated" not in capped
    assert big.count("\n") + 1 == 50  # source unchanged


def test_short_output_is_not_truncated() -> None:
    from core.agent_harness.turns.display_text import cap_for_display

    text = "total 56\ndrwxr-xr-x  15 user"
    assert cap_for_display(text) == text


def test_character_cap_reports_truncation_when_lines_fit() -> None:
    from core.agent_harness.turns.display_text import (
        DISPLAY_OUTPUT_MAX_CHARS,
        cap_for_display,
    )
    from infrastructure.terminal.peek import format_view_all_marker

    text = "x" * (DISPLAY_OUTPUT_MAX_CHARS + 40)
    capped = cap_for_display(text)
    assert capped.endswith(format_view_all_marker())
    body, _, marker = capped.rpartition("\n")
    assert marker == format_view_all_marker()
    assert body.endswith("…")
    assert len(body) <= DISPLAY_OUTPUT_MAX_CHARS + 1
    assert "more, Ctrl+O" not in capped


def test_character_and_line_caps_share_one_marker() -> None:
    from core.agent_harness.turns.display_text import (
        DISPLAY_OUTPUT_MAX_CHARS,
        DISPLAY_OUTPUT_MAX_LINES,
        cap_for_display,
    )
    from infrastructure.terminal.peek import format_expand_marker, format_view_all_marker

    # First N lines together exceed the character cap, and extra lines remain.
    line = "y" * ((DISPLAY_OUTPUT_MAX_CHARS // 2) + 1)
    extra_lines = 3
    text = "\n".join([line] * (DISPLAY_OUTPUT_MAX_LINES + extra_lines))
    capped = cap_for_display(text)

    assert format_expand_marker(extra_lines) in capped
    assert format_view_all_marker() not in capped
    assert "output truncated" not in capped
    body, _, marker = capped.rpartition("\n")
    assert marker == format_expand_marker(extra_lines)
    assert body.endswith("…")
    assert len(body) <= DISPLAY_OUTPUT_MAX_CHARS + 1
