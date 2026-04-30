"""Tests for the GitLab MR write-back helper."""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.nodes.publish_findings.gitlab_writeback import _build_mr_note, post_gitlab_mr_writeback


@pytest.fixture()
def state_with_gitlab():
    return {
        "available_sources": {
            "gitlab": {
                "merge_request_iid": "42",
                "project_id": "99",
                "gitlab_url": "https://gitlab.example.com",
                "gitlab_token": "glpat-test",
            }
        }
    }


def test_build_mr_note_short_message():
    note = _build_mr_note("Hello world")
    assert "Hello world" in note
    assert "<details>" in note


def test_build_mr_note_truncates_long_message():
    long_msg = "x" * 5000
    note = _build_mr_note(long_msg)
    assert "x" * 3997 + "..." in note
    assert "x" * 3998 not in note


def test_build_mr_note_body_capped_at_4000_chars():
    long_msg = "x" * 5000
    note = _build_mr_note(long_msg)
    body = note.split("<summary>Investigation summary</summary>\n\n")[1].split("\n\n</details>")[0]
    assert len(body) == 4000
    assert body.endswith("...")


def test_no_op_when_env_flag_off(state_with_gitlab):
    with (
        patch.dict(os.environ, {"GITLAB_MR_WRITEBACK": "false"}),
        patch("app.nodes.publish_findings.gitlab_writeback.post_gitlab_mr_note") as mock_post,
    ):
        post_gitlab_mr_writeback(state_with_gitlab, "report")
        mock_post.assert_not_called()


def test_no_op_when_mr_iid_missing():
    state = {"available_sources": {"gitlab": {"project_id": "99"}}}
    with (
        patch.dict(os.environ, {"GITLAB_MR_WRITEBACK": "true"}),
        patch("app.nodes.publish_findings.gitlab_writeback.post_gitlab_mr_note") as mock_post,
    ):
        post_gitlab_mr_writeback(state, "report")
        mock_post.assert_not_called()


def test_no_op_when_project_id_missing():
    state = {"available_sources": {"gitlab": {"merge_request_iid": "42"}}}
    with (
        patch.dict(os.environ, {"GITLAB_MR_WRITEBACK": "true"}),
        patch("app.nodes.publish_findings.gitlab_writeback.post_gitlab_mr_note") as mock_post,
    ):
        post_gitlab_mr_writeback(state, "report")
        mock_post.assert_not_called()


def test_failure_does_not_propagate(state_with_gitlab):
    with (
        patch.dict(os.environ, {"GITLAB_MR_WRITEBACK": "true"}),
        patch(
            "app.nodes.publish_findings.gitlab_writeback.post_gitlab_mr_note",
            side_effect=RuntimeError("network error"),
        ),
        patch(
            "app.nodes.publish_findings.gitlab_writeback.build_gitlab_config",
            return_value=MagicMock(),
        ),
        patch("app.nodes.publish_findings.gitlab_writeback.logger") as mock_logger,
    ):
        post_gitlab_mr_writeback(state_with_gitlab, "report")
        mock_logger.warning.assert_called_once()


def test_happy_path_calls_post_mr_note(state_with_gitlab):
    mock_config = MagicMock()
    with (
        patch.dict(os.environ, {"GITLAB_MR_WRITEBACK": "true"}),
        patch(
            "app.nodes.publish_findings.gitlab_writeback.build_gitlab_config",
            return_value=mock_config,
        ) as mock_build,
        patch("app.nodes.publish_findings.gitlab_writeback.post_gitlab_mr_note") as mock_post,
    ):
        post_gitlab_mr_writeback(state_with_gitlab, "the report")

        mock_build.assert_called_once_with(
            {"base_url": "https://gitlab.example.com", "auth_token": "glpat-test"}
        )
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["project_id"] == "99"
        assert call_kwargs["mr_iid"] == "42"
        assert "the report" in call_kwargs["body"]
