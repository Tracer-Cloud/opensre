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


LOCAL_REPORT = (
    "GitHub couldn't be read from this machine, so here's what your local history shows instead.\n\n"
    "4 repositories · 650 commits by you in the last 30 days.\n\n"
    "### What stands out\n"
    "- **Follow-up fixes:** 66 of your 650 commits (10%) fixed files you had changed less than an "
    "hour earlier, 59 of them in acme/secret-payments.\n"
    "- **AI pairing:** 418 of your 650 commits (64%) were co-authored by an AI agent."
)


def test_local_report_records_the_stored_summary_never_the_repository_names():
    """The reply names private repositories; the recorded value is the tool's name-free summary."""
    session = SimpleNamespace(
        active_skill="analyzing-local-repositories",
        skill_value_notes={
            "Follow-up fixes": (
                "follow_up_fixes",
                "10% of own commits (66 of 650) fixed files changed less than an hour earlier",
            ),
            "AI pairing": ("ai_pairing", "418 of your 650 commits (64%) were co-authored"),
        },
    )
    seen: set[str] = set()
    with patch("core.agent_harness.turns.skill_value.capture_skill_value_delivered") as capture:
        record_skill_value(session, LOCAL_REPORT, seen)
        record_skill_value(session, LOCAL_REPORT, seen)

    assert capture.call_count == 1
    kwargs = capture.call_args.kwargs
    assert kwargs["skill_name"] == "analyzing-local-repositories"
    assert kwargs["insight_kind"] == "follow_up_fixes"
    assert kwargs["insight"] == (
        "Follow-up fixes: 10% of own commits (66 of 650) fixed files changed less than an hour earlier"
    )
    assert "secret-payments" not in kwargs["insight"]


def test_local_report_without_a_known_label_or_notes_records_nothing():
    session = SimpleNamespace(active_skill="analyzing-local-repositories", skill_value_notes={})
    with patch("core.agent_harness.turns.skill_value.capture_skill_value_delivered") as capture:
        record_skill_value(session, LOCAL_REPORT, set())
        session.skill_value_notes = {"Follow-up fixes": ("follow_up_fixes", "summary")}
        record_skill_value(
            session, LOCAL_REPORT.replace("**Follow-up fixes:**", "**Made up:**"), set()
        )
    assert not capture.called
