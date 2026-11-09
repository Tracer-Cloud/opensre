"""Tests for the Sentry issue-fix tool: gating, context, lifecycle, error_kind."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx

from integrations.coding_agent import CodingResult
from integrations.sentry import SentryConfig
from tools.cross_vendor.fix_sentry_issue import FixSentryIssueTool, fix_sentry_issue
from tools.cross_vendor.fix_sentry_issue.context import gather_issue_context

_CONFIG = "tools.cross_vendor.fix_sentry_issue.context.sentry_config_from_env"
_GET_ISSUE = "tools.cross_vendor.fix_sentry_issue.context.get_sentry_issue"
_VERIFY = "tools.cross_vendor.fix_sentry_issue.runner.verify_coding_agent"
_RUN = "tools.cross_vendor.fix_sentry_issue.runner.run_coding_task"

_ISSUE = {
    "title": "TypeError: 'NoneType' object is not subscriptable",
    "culprit": "app.handlers.process_event",
    "level": "error",
    "count": "42",
    "metadata": {
        "type": "TypeError",
        "value": "'NoneType' object is not subscriptable",
        "filename": "app/handlers.py",
        "function": "process_event",
    },
}
_URL = "https://acme.sentry.io/issues/12345/"


# --------------------------------------------------------------------------- #
# metadata + availability
# --------------------------------------------------------------------------- #
def test_metadata_is_mutating_on_action_surface() -> None:
    t = fix_sentry_issue
    assert t.name == "fix_sentry_issue"
    assert t.source == "sentry"
    assert t.side_effect_level == "mutating"
    assert t.requires_approval is False
    assert t.surfaces == ("action",)
    assert t.input_schema["required"] == ["sentry_url"]
    assert "error_kind" in t.outputs
    assert t.metadata().name == "fix_sentry_issue"


def test_is_available_off_by_default_then_opt_in() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PI_ISSUE_FIX_ENABLED", None)
        assert fix_sentry_issue.is_available({}) is False
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        assert fix_sentry_issue.is_available({}) is True


# --------------------------------------------------------------------------- #
# context building
# --------------------------------------------------------------------------- #
@patch(_GET_ISSUE, return_value=_ISSUE)
@patch(_CONFIG, return_value=MagicMock())
def test_gather_issue_context_builds_masked_task(
    _mock_cfg: MagicMock, _mock_issue: MagicMock
) -> None:
    ctx = gather_issue_context(_URL)
    assert ctx.issue_id == "12345"
    assert "Issue:" in ctx.task
    assert "TypeError" in ctx.task
    assert "app/handlers.py" in ctx.task


@patch(_GET_ISSUE, return_value=_ISSUE)
@patch(_CONFIG, return_value=SentryConfig(organization_slug="env-org", auth_token="tok"))
def test_gather_uses_org_from_url_over_config(_mock_cfg: MagicMock, mock_issue: MagicMock) -> None:
    # URL org is "acme"; config org is "env-org" — the URL must win.
    gather_issue_context("https://acme.sentry.io/issues/12345/")
    used_config = mock_issue.call_args.kwargs["config"]
    assert used_config.organization_slug == "acme"


# --------------------------------------------------------------------------- #
# run() lifecycle + error_kind
# --------------------------------------------------------------------------- #
def test_run_disabled() -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("PI_ISSUE_FIX_ENABLED", None)
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["success"] is False
    assert out["error_kind"] == "disabled"


def test_run_invalid_url() -> None:
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url="https://github.com/foo/bar/issues/1")
    assert out["error_kind"] == "invalid_input"


@patch(_CONFIG, return_value=None)
def test_run_sentry_unavailable(_mock_cfg: MagicMock) -> None:
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["error_kind"] == "sentry_unavailable"


@patch(_GET_ISSUE, return_value={})
@patch(_CONFIG, return_value=MagicMock())
def test_run_issue_not_found_empty_response(_mock_cfg: MagicMock, _mock_issue: MagicMock) -> None:
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["error_kind"] == "issue_not_found"


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://acme.sentry.io/api/0/issues/12345/")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, request=req))


@patch(_GET_ISSUE, side_effect=_http_status_error(404))
@patch(_CONFIG, return_value=MagicMock())
def test_run_issue_not_found_on_http_404(_mock_cfg: MagicMock, _mock_issue: MagicMock) -> None:
    # The common real failure: valid URL, issue id doesn't exist -> Sentry 404.
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["error_kind"] == "issue_not_found"


@patch(_GET_ISSUE, side_effect=_http_status_error(403))
@patch(_CONFIG, return_value=MagicMock())
def test_run_sentry_auth_error_on_http_403(_mock_cfg: MagicMock, _mock_issue: MagicMock) -> None:
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["error_kind"] == "sentry_unavailable"


@patch(_GET_ISSUE, side_effect=httpx.ConnectError("boom"))
@patch(_CONFIG, return_value=MagicMock())
def test_run_sentry_network_error(_mock_cfg: MagicMock, _mock_issue: MagicMock) -> None:
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["error_kind"] == "sentry_unavailable"


@patch(_VERIFY, return_value=(False, "pi not installed"))
@patch(_GET_ISSUE, return_value=_ISSUE)
@patch(_CONFIG, return_value=MagicMock())
def test_run_cli_unavailable(
    _mock_cfg: MagicMock, _mock_issue: MagicMock, _mock_verify: MagicMock
) -> None:
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["error_kind"] == "cli_unavailable"


@patch(_RUN)
@patch(_VERIFY, return_value=(True, "ok"))
@patch(_GET_ISSUE, return_value=_ISSUE)
@patch(_CONFIG, return_value=MagicMock())
def test_run_success(
    _mock_cfg: MagicMock, _mock_issue: MagicMock, _mock_verify: MagicMock, mock_run: MagicMock
) -> None:
    mock_run.return_value = CodingResult(
        success=True,
        summary="Guarded the None case in process_event.",
        changed_files=["app/handlers.py"],
        diff="diff --git a/app/handlers.py b/app/handlers.py\n",
        returncode=0,
    )
    with patch.dict(os.environ, {"PI_ISSUE_FIX_ENABLED": "1"}, clear=False):
        out = fix_sentry_issue.run(sentry_url=_URL)
    assert out["success"] is True
    assert out["error_kind"] is None
    assert out["issue_id"] == "12345"
    assert out["changed_files"] == ["app/handlers.py"]
    assert "diff --git" in out["diff"]
    # the synthesized Sentry task was passed to the Pi client
    assert "Sentry issue" in mock_run.call_args.args[0]


# --------------------------------------------------------------------------- #
# registry discovery
# --------------------------------------------------------------------------- #
def test_registry_discovers_fix_sentry_issue_on_action_surface() -> None:
    from tools.registry import get_registered_tool_map

    action = get_registered_tool_map("action")
    chat = get_registered_tool_map("chat")
    assert "fix_sentry_issue" in action
    assert "fix_sentry_issue" not in chat
    rt = action["fix_sentry_issue"]
    assert rt.requires_approval is False
    assert rt.side_effect_level == "mutating"


def test_tool_subclass_constructs() -> None:
    assert isinstance(fix_sentry_issue, FixSentryIssueTool)
