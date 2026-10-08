"""Security and field visibility contracts for approval previews."""

from __future__ import annotations

import json
from collections.abc import Iterator

from infrastructure.observability.trace.approval_preview import format_approval_preview


def test_large_values_cannot_hide_later_nested_fields_or_list_targets() -> None:
    arguments = {
        "tool_name": "restart_service",
        "arguments": {
            "padding": "x" * 10000,
            "targets": [{"service": "api"}, {"service": "prod"}],
            "message": "Bearer sample-token",
        },
    }
    preview = format_approval_preview(arguments, max_chars=400)
    assert preview.fields_visible and len(preview.text) <= 400
    shown = json.loads(preview.text)
    assert shown["arguments"]["targets"] == arguments["arguments"]["targets"]
    assert set(shown["arguments"]) == set(arguments["arguments"])
    assert "sample-token" not in preview.text
    assert "[truncated]" in shown["arguments"]["padding"]
    assert preview.truncated
    full = json.loads(preview.full_text)
    assert full["arguments"]["padding"] == arguments["arguments"]["padding"]
    assert "sample-token" not in preview.full_text
    assert arguments["arguments"]["padding"] == "x" * 10000


def test_structure_that_cannot_fit_is_explicitly_unreviewable() -> None:
    preview = format_approval_preview({f"field_{i}": i for i in range(100)}, max_chars=400)
    assert not preview.fields_visible
    assert len(preview.text) <= 400
    assert "fields" in preview.text


def test_review_limit_and_non_json_values_never_authorize_partial_evidence() -> None:
    for value in ({"query": "x" * 64000}, {"code": object()}):
        preview = format_approval_preview(value, max_chars=400)
        assert not preview.fields_visible
        assert not preview.full_text


def test_oversized_review_stops_before_copying_the_complete_argument_tree() -> None:
    class BudgetedList(list[object]):
        def __iter__(self) -> Iterator[object]:
            for _ in range(100):
                yield "x" * 1000
            raise AssertionError("approval arguments traversed beyond the review budget")

    preview = format_approval_preview(BudgetedList([None]), max_chars=400)

    assert not preview.fields_visible
    assert not preview.full_text
    assert "complete review limit" in preview.text


def test_keys_after_underscores_are_redacted_without_matching_ordinary_task_names() -> None:
    key = "sk-" + "a" * 32
    ordinary = "task-service-deployment-production"
    preview = format_approval_preview(
        {"query": "x" * 5000 + "prefix_" + key + " " + ordinary},
        max_chars=400,
    )
    assert key not in preview.full_text
    assert ordinary in preview.full_text


def test_secret_field_names_do_not_leak_or_merge_distinct_argument_fields() -> None:
    first = "sk-" + "a" * 32
    second = "sk-" + "b" * 32
    arguments = {first: "first-target", second: "second-target"}
    preview = format_approval_preview(arguments, max_chars=400)
    assert first not in preview.full_text and second not in preview.full_text
    assert sorted(json.loads(preview.full_text).values()) == ["first-target", "second-target"]
    assert list(arguments) == [first, second]


def test_complete_review_masks_url_userinfo_and_basic_auth_diagnostics() -> None:
    preview = format_approval_preview(
        {
            "query": "x" * 5000 + "https://user:opaque-password@example.test/mcp "
            "Authorization: Basic dXNlcjpwYXNzd29yZA==",
            "endpoint": "https://example.test/mcp?email=user@example.test",
        },
        max_chars=400,
    )
    assert "opaque-password" not in preview.full_text
    assert "dXNlcjpwYXNzd29yZA==" not in preview.full_text
    assert "https://example.test/mcp?email=user@example.test" in preview.full_text


def test_complete_review_masks_credentials_embedded_under_neutral_keys() -> None:
    preview = format_approval_preview(
        {"header": "xapp-AAAAAAAAAAAAAAAAAAAA", "query": "api_key=secret123"},
        max_chars=400,
    )

    assert "xapp-AAAAAAAAAAAAAAAAAAAA" not in preview.full_text
    assert "secret123" not in preview.full_text
