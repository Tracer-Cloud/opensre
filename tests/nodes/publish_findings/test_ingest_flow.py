"""Tests for the collapsed ingest helper introduced in issue #867.

Covers:
- ``ingest_investigation_with_url`` in ``app.utils.ingest_delivery``
- ``_create_investigation_and_attach_url`` in ``app.nodes.publish_findings.node``

Acceptance criteria (from issue #867):
- ``generate_report()`` no longer contains the duplicated ingest sequence inline.
- Failure behaviour stays the same.
- The helper name clearly explains the two-step flow.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import patch

import pytest

from app.nodes.publish_findings.node import _create_investigation_and_attach_url
from app.state import InvestigationState
from app.utils.ingest_delivery import ingest_investigation_with_url

# ---------------------------------------------------------------------------
# Minimal state factory
# ---------------------------------------------------------------------------


def _make_state(**overrides: Any) -> InvestigationState:
    base: dict[str, Any] = {
        "org_id": "org-123",
        "alert_name": "HighCPU",
        "thread_id": "T001",
        "run_id": "run-001",
        "severity": "high",
        "organization_slug": "acme",
        "summary": None,
        "problem_md": None,
        "root_cause": None,
        "raw_alert": {},
        "planned_actions": [],
        "investigation_recommendations": [],
        "validity_score": 0,
        "pipeline_name": "",
        "problem_report": None,
    }
    base.update(overrides)
    return cast(InvestigationState, base)


# ===========================================================================
# ingest_investigation_with_url
# ===========================================================================


class TestIngestInvestigationWithUrl:
    """Unit tests for ``ingest_investigation_with_url``."""

    def test_happy_path_calls_send_ingest_twice(self) -> None:
        """Both ingest calls are made when the first returns an investigation_id."""
        state = _make_state()

        with patch("app.utils.ingest_delivery.send_ingest", return_value="inv-abc") as mock_send:
            result = ingest_investigation_with_url(
                state,
                report_md="# Report",
                summary="short summary",
                investigation_url="https://app.tracer.cloud/inv/inv-abc",
            )

        assert result == "inv-abc"
        assert mock_send.call_count == 2

        # First call: report only, no URL
        first_state = mock_send.call_args_list[0][0][0]
        assert first_state["problem_report"] == {"report_md": "# Report"}
        assert first_state["summary"] == "short summary"

        # Second call: report + URL attached
        second_state = mock_send.call_args_list[1][0][0]
        assert second_state["problem_report"] == {
            "report_md": "# Report",
            "investigation_url": "https://app.tracer.cloud/inv/inv-abc",
        }

    def test_no_investigation_id_skips_url_attachment(self) -> None:
        """When the first ingest returns None, no URL-attachment call is made."""
        state = _make_state()

        with patch("app.utils.ingest_delivery.send_ingest", return_value=None) as mock_send:
            result = ingest_investigation_with_url(
                state,
                report_md="# Report",
                summary=None,
                investigation_url="https://app.tracer.cloud/inv/whatever",
            )

        assert result is None
        assert mock_send.call_count == 1

    def test_no_url_skips_second_ingest(self) -> None:
        """When investigation_url is None, only the first ingest is called."""
        state = _make_state()

        with patch("app.utils.ingest_delivery.send_ingest", return_value="inv-xyz") as mock_send:
            result = ingest_investigation_with_url(
                state,
                report_md="# Report",
                summary=None,
                investigation_url=None,
            )

        assert result == "inv-xyz"
        assert mock_send.call_count == 1

    def test_first_ingest_raises_returns_none_and_skips_url_step(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If the first send_ingest raises, None is returned and the URL step is skipped."""
        state = _make_state()

        with (
            patch(
                "app.utils.ingest_delivery.send_ingest",
                side_effect=RuntimeError("network error"),
            ) as mock_send,
            caplog.at_level("WARNING", logger="app.utils.ingest_delivery"),
        ):
            result = ingest_investigation_with_url(
                state,
                report_md="# Report",
                summary=None,
                investigation_url="https://app.tracer.cloud/inv/x",
            )

        assert result is None
        assert mock_send.call_count == 1
        assert "initial ingest failed" in caplog.text

    def test_second_ingest_raises_still_returns_investigation_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """If the URL-attachment call fails, the investigation_id is still returned."""
        state = _make_state()

        send_results = ["inv-999", RuntimeError("timeout")]

        def _side_effect(*_args: Any, **_kwargs: Any) -> str | None:
            val = send_results.pop(0)
            if isinstance(val, Exception):
                raise val
            return str(val)

        with (
            patch("app.utils.ingest_delivery.send_ingest", side_effect=_side_effect),
            caplog.at_level("WARNING", logger="app.utils.ingest_delivery"),
        ):
            result = ingest_investigation_with_url(
                state,
                report_md="# Report",
                summary=None,
                investigation_url="https://app.tracer.cloud/inv/inv-999",
            )

        assert result == "inv-999"
        assert "url attachment ingest failed" in caplog.text

    def test_state_is_not_mutated(self) -> None:
        """The original state dict must not be modified by the helper."""
        state = _make_state()
        original_keys = set(state.keys())
        original_report = state.get("problem_report")

        with patch("app.utils.ingest_delivery.send_ingest", return_value="inv-001"):
            ingest_investigation_with_url(
                state,
                report_md="# Report",
                summary="s",
                investigation_url="https://app.tracer.cloud/inv/inv-001",
            )

        assert set(state.keys()) == original_keys
        assert state.get("problem_report") == original_report


# ===========================================================================
# _create_investigation_and_attach_url  (node-level helper)
# ===========================================================================


class TestCreateInvestigationAndAttachUrl:
    """Unit tests for ``_create_investigation_and_attach_url`` in node.py."""

    def test_returns_id_and_url_on_success(self) -> None:
        """Returns a (investigation_id, investigation_url) tuple on full success."""
        state = _make_state(organization_slug="acme")

        with (
            patch("app.nodes.publish_findings.node.send_ingest", return_value="inv-42"),
            patch(
                "app.nodes.publish_findings.node.get_investigation_url",
                return_value="https://app.tracer.cloud/acme/inv-42",
            ),
        ):
            inv_id, inv_url = _create_investigation_and_attach_url(
                state, report_md="# Report", summary="s"
            )

        assert inv_id == "inv-42"
        assert inv_url == "https://app.tracer.cloud/acme/inv-42"

    def test_returns_none_id_when_first_ingest_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the first ingest raises, returns (None, url_for_None)."""
        state = _make_state(organization_slug="acme")

        with (
            patch(
                "app.nodes.publish_findings.node.send_ingest",
                side_effect=RuntimeError("down"),
            ),
            patch(
                "app.nodes.publish_findings.node.get_investigation_url",
                return_value=None,
            ),
            caplog.at_level("WARNING", logger="app.nodes.publish_findings.node"),
        ):
            inv_id, inv_url = _create_investigation_and_attach_url(
                state, report_md="# Report", summary=None
            )

        assert inv_id is None
        assert inv_url is None
        assert "ingest failed" in caplog.text

    def test_url_attachment_failure_still_returns_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the first ingest succeeds but the URL attachment raises, the id is returned."""
        state = _make_state(organization_slug="acme")

        call_count = 0

        def _send_ingest(s: Any) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "inv-77"
            raise RuntimeError("url attach failed")

        with (
            patch("app.nodes.publish_findings.node.send_ingest", side_effect=_send_ingest),
            patch(
                "app.nodes.publish_findings.node.get_investigation_url",
                return_value="https://app.tracer.cloud/acme/inv-77",
            ),
            caplog.at_level("WARNING", logger="app.nodes.publish_findings.node"),
        ):
            inv_id, inv_url = _create_investigation_and_attach_url(
                state, report_md="# Report", summary="s"
            )

        assert inv_id == "inv-77"
        assert inv_url == "https://app.tracer.cloud/acme/inv-77"
        assert "url attachment" in caplog.text

    def test_no_id_means_no_url_attachment_call(self) -> None:
        """When first ingest returns None, send_ingest is called exactly once."""
        state = _make_state(organization_slug="acme")

        with (
            patch("app.nodes.publish_findings.node.send_ingest", return_value=None) as mock_send,
            patch(
                "app.nodes.publish_findings.node.get_investigation_url",
                return_value=None,
            ),
        ):
            inv_id, inv_url = _create_investigation_and_attach_url(
                state, report_md="# Report", summary=None
            )

        assert inv_id is None
        assert mock_send.call_count == 1

    def test_send_ingest_called_twice_on_happy_path(self) -> None:
        """Exactly two send_ingest calls are made when everything succeeds."""
        state = _make_state(organization_slug="acme")

        with (
            patch("app.nodes.publish_findings.node.send_ingest", return_value="inv-1") as mock_send,
            patch(
                "app.nodes.publish_findings.node.get_investigation_url",
                return_value="https://app.tracer.cloud/acme/inv-1",
            ),
        ):
            _create_investigation_and_attach_url(state, report_md="# Report", summary="s")

        assert mock_send.call_count == 2


# ===========================================================================
# Structural: generate_report must not contain inline two-step sequence
# ===========================================================================


class TestGenerateReportStructure:
    """Verify the acceptance criteria about generate_report's structure."""

    def test_generate_report_delegates_to_helper_not_inline_sequence(self) -> None:
        """generate_report must call _create_investigation_and_attach_url, not send_ingest directly."""
        import inspect

        from app.nodes.publish_findings import node as node_module

        source = inspect.getsource(node_module.generate_report)

        # The inline two-call sequence is gone
        assert source.count("send_ingest(") == 0, (
            "generate_report() should not call send_ingest() directly; "
            "delegate to _create_investigation_and_attach_url instead."
        )

        # The helper is used
        assert "_create_investigation_and_attach_url(" in source, (
            "generate_report() must delegate to _create_investigation_and_attach_url()."
        )
