"""An insight is delivered only when the report reaches the output sink."""

from types import SimpleNamespace
from unittest.mock import patch

from core.agent_harness.turns.action_driver import _deferred_reply_presenter
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from core.agent_harness.turns.skill_value import ci_performance_insight, record_skill_value

INSIGHT = "CI-caused failures account for **0.5% of all PR runs**, roughly **3.2–3.8× lower** than the comparison repositories."
REPORT = "What insights stand out:\n- " + INSIGHT + "\n\nThe report is delivered."


def test_exact_insight_and_wrapped_bullet_stop_at_next_paragraph():
    assert ci_performance_insight(REPORT) == INSIGHT
    assert (
        ci_performance_insight(
            "**What insights stand out:**\n- CI failures are low,\n  compared with the sample.\n\nNext steps"
        )
        == "CI failures are low, compared with the sample."
    )
    assert ci_performance_insight("What insights stand out:\n- x.x% of runs") == ""
    assert ci_performance_insight(INSIGHT) == ""
    assert ci_performance_insight(REPORT.replace("\n\n", "\n")) == INSIGHT


def test_successfully_displayed_report_is_captured_once():
    session = SimpleNamespace(active_skill="analyzing-github-ci-performance")
    seen = set()
    replies = []
    with patch("core.agent_harness.turns.skill_value.capture_skill_value_delivered") as capture:
        presenter = _deferred_reply_presenter(
            BufferOutputSink(), replies, lambda text: record_skill_value(session, text, seen)
        )
        assert presenter(REPORT)
        assert presenter(REPORT)
        assert capture.call_count == 1
        assert capture.call_args.kwargs["insight"] == INSIGHT
        assert capture.call_args.kwargs["skill_name"] == session.active_skill


def test_failed_output_and_other_skills_do_not_deliver_value():
    class BrokenSink(BufferOutputSink):
        def stream(self, *_args, **_kwargs):
            raise OSError("terminal unavailable")

    session = SimpleNamespace(active_skill="analyzing-github-ci-performance")
    with patch("core.agent_harness.turns.skill_value.capture_skill_value_delivered") as capture:
        presenter = _deferred_reply_presenter(
            BrokenSink(), [], lambda text: record_skill_value(session, text, set())
        )
        assert not presenter(REPORT)
        session.active_skill = "other-skill"
        record_skill_value(session, REPORT, set())
        assert not capture.called
