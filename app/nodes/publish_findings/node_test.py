"""Tests for publish_findings node — _build_mr_note and GitLab MR write-back."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.nodes.publish_findings.node import _build_mr_note

# ---------------------------------------------------------------------------
# _build_mr_note
# ---------------------------------------------------------------------------


def test_build_mr_note_wraps_in_details_block() -> None:
    result = _build_mr_note("root cause is X")

    assert "<details>" in result
    assert "<summary>Investigation summary</summary>" in result
    assert "root cause is X" in result
    assert "### RCA Finding" in result


def test_build_mr_note_truncates_long_messages() -> None:
    long_message = "x" * 5000

    result = _build_mr_note(long_message)

    assert len(result) < 5000 + 200  # body capped + wrapper overhead
    assert result.endswith("</details>")
    assert "..." in result


def test_build_mr_note_does_not_truncate_short_messages() -> None:
    message = "short message"

    result = _build_mr_note(message)

    assert "..." not in result
    assert message in result


# ---------------------------------------------------------------------------
# GitLab MR write-back in generate_report
# ---------------------------------------------------------------------------


def _make_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "problem_md": "something broke",
        "root_cause_category": "infra",
        "slack_context": {},
        "organization_slug": None,
        "available_sources": {
            "gitlab": {
                "project_id": "my-org/my-repo",
                "merge_request_iid": "5",
                "gitlab_url": "https://gitlab.example.com/api/v4",
                "gitlab_token": "gl-token",
            }
        },
        "resolved_integrations": {},
    }
    base.update(overrides)
    return base


def _patch_generate_report_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch all heavy dependencies of generate_report so we can run it in tests."""
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.build_report_context",
        lambda _state: {},
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.format_slack_message",
        lambda _ctx: "slack report text",
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.build_slack_blocks",
        lambda _ctx: [],
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.get_investigation_url",
        lambda _slug, _inv_id: "https://app.example.com/inv/1",
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.render_report",
        lambda _msg, **_kw: None,
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.open_in_editor",
        lambda _msg: None,
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.send_ingest",
        lambda _state: "inv-id-123",
    )


def test_gitlab_writeback_calls_post_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_generate_report_deps(monkeypatch)
    monkeypatch.setenv("GITLAB_MR_WRITEBACK", "true")

    mock_send_slack = MagicMock(return_value=(False, None))
    mock_post_note = MagicMock(return_value={"id": 1})
    mock_build_action_blocks = MagicMock(return_value=[])

    with (
        patch("app.utils.slack_delivery.send_slack_report", mock_send_slack),
        patch("app.utils.slack_delivery.build_action_blocks", mock_build_action_blocks),
        patch("app.integrations.gitlab.post_gitlab_mr_note", mock_post_note),
        patch("app.integrations.gitlab.build_gitlab_config", return_value=MagicMock()),
    ):
        from app.nodes.publish_findings.node import generate_report
        generate_report(_make_state())  # type: ignore[arg-type]

    mock_post_note.assert_called_once()
    _, kwargs = mock_post_note.call_args
    assert kwargs["project_id"] == "my-org/my-repo"
    assert kwargs["mr_iid"] == "5"


def test_gitlab_writeback_skipped_when_env_var_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_generate_report_deps(monkeypatch)
    monkeypatch.delenv("GITLAB_MR_WRITEBACK", raising=False)

    mock_post_note = MagicMock()
    mock_send_slack = MagicMock(return_value=(False, None))
    mock_build_action_blocks = MagicMock(return_value=[])

    with (
        patch("app.utils.slack_delivery.send_slack_report", mock_send_slack),
        patch("app.utils.slack_delivery.build_action_blocks", mock_build_action_blocks),
        patch("app.integrations.gitlab.post_gitlab_mr_note", mock_post_note),
    ):
        from app.nodes.publish_findings.node import generate_report
        generate_report(_make_state())  # type: ignore[arg-type]

    mock_post_note.assert_not_called()


def test_gitlab_writeback_skipped_when_mr_iid_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_generate_report_deps(monkeypatch)
    monkeypatch.setenv("GITLAB_MR_WRITEBACK", "true")

    state = _make_state(available_sources={"gitlab": {"project_id": "my-org/my-repo", "merge_request_iid": ""}})
    mock_post_note = MagicMock()
    mock_send_slack = MagicMock(return_value=(False, None))
    mock_build_action_blocks = MagicMock(return_value=[])

    with (
        patch("app.utils.slack_delivery.send_slack_report", mock_send_slack),
        patch("app.utils.slack_delivery.build_action_blocks", mock_build_action_blocks),
        patch("app.integrations.gitlab.post_gitlab_mr_note", mock_post_note),
    ):
        from app.nodes.publish_findings.node import generate_report
        generate_report(state)  # type: ignore[arg-type]

    mock_post_note.assert_not_called()


def test_gitlab_writeback_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_generate_report_deps(monkeypatch)
    monkeypatch.setenv("GITLAB_MR_WRITEBACK", "true")

    mock_send_slack = MagicMock(return_value=(False, None))
    mock_build_action_blocks = MagicMock(return_value=[])

    with (
        patch("app.utils.slack_delivery.send_slack_report", mock_send_slack),
        patch("app.utils.slack_delivery.build_action_blocks", mock_build_action_blocks),
        patch("app.integrations.gitlab.post_gitlab_mr_note", side_effect=RuntimeError("network error")),
        patch("app.integrations.gitlab.build_gitlab_config", return_value=MagicMock()),
    ):
        from app.nodes.publish_findings.node import generate_report
        result = generate_report(_make_state())  # type: ignore[arg-type]

    assert "slack_message" in result  # report returned despite write-back failure
