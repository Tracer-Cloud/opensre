"""A ``shell_run`` turn keeps the model's grounded closing.

Quiet ``shell_run`` withheld live stdout, so its closing *is* the display. A
loud ``shell_run`` also keeps its closing (grounded in the stdout/exit the model
observed), but its already-painted stdout is not reprinted under it. Raw command
output never enters display_chunks; the composed closing does.
"""

from __future__ import annotations

import json
from typing import Any

from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts
from core.agent_harness.turns.display_text import is_outcome_report
from core.llm.types import ToolCall
from core.messages import AssistantRuntimeMessage


class _ToolResult:
    def __init__(self, payload: dict[str, Any], *, is_error: bool = False) -> None:
        self.content = json.dumps(payload)
        self.details = payload
        self.is_error = is_error


class _Result:
    def __init__(
        self,
        *,
        tool_results: list[tuple[ToolCall, _ToolResult]],
        final_text: str = "",
        messages: list[Any] | None = None,
    ) -> None:
        self.tool_results = tool_results
        self.executed = list(tool_results)
        self.final_text = final_text
        self.planned = [call for call, _ in tool_results]
        self.messages = messages or []


class _Session:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []
        self.pending_user_choice: object | None = None

        class _Terminal:
            pending_choice_response: str | None = None

        self.terminal = _Terminal()


def _shell_call(call_id: str, command: str, *, quiet: bool) -> ToolCall:
    return ToolCall(id=call_id, name="shell_run", input={"command": command, "quiet": quiet})


def _payload(response_text: str) -> dict[str, Any]:
    return {"ok": True, "response_text": response_text}


def _counts(
    steps: int,
    *,
    executed_entries: list[dict[str, Any]] | None = None,
) -> _TurnCounts:
    return _TurnCounts(
        executed_entries=executed_entries or [],
        executed_count=steps,
        executed_success_count=steps,
        generic_success_count=0,
        planned_count=steps,
        handled=True,
    )


def test_single_quiet_shell_run_keeps_the_model_closing() -> None:
    # Arrange: quiet withheld live stdout; the closing is the turn display.
    closing = "Amsterdam is +18C and clear."
    call = _shell_call("1", "curl wttr.in", quiet=True)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("Amsterdam: +18C")))],
        final_text=closing,
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: closing shown; raw probe stdout is not reprinted by core.
    shown = "\n".join(display_chunks)
    assert closing in shown
    assert "Amsterdam: +18C" not in shown


def test_quiet_string_false_is_coerced_to_loud() -> None:
    # Arrange: models sometimes emit quiet as a string; "false" must read as loud
    # (already-on-screen stdout), not as a quiet probe.
    call = ToolCall(
        id="1",
        name="shell_run",
        input={"command": "echo hi", "quiet": "false"},
    )
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi")))],
        final_text="done",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: the grounded closing shows; the loud stdout is not reprinted under it.
    shown = "\n".join(display_chunks)
    assert "done" in shown
    assert "hi" not in shown


def test_quiet_probes_stay_hidden_when_a_composed_closing_is_shown() -> None:
    # Arrange: a multi-step chain keeps its closing, so the probes stay hidden.
    closing = "Amsterdam: sunny. Top story: markets open higher."
    result = _Result(
        tool_results=[
            (
                _shell_call("1", "curl wttr.in", quiet=True),
                _ToolResult(_payload("Amsterdam: +18C")),
            ),
            (
                _shell_call("2", "curl news", quiet=True),
                _ToolResult(_payload("Markets open higher")),
            ),
        ],
        final_text=closing,
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )

    # Assert: the composed answer only — no raw curl output under it.
    shown = "\n".join(display_chunks)
    assert closing in shown
    assert "Amsterdam: +18C" not in shown
    assert "Markets open higher" not in shown


def test_loud_shell_run_keeps_closing_without_reprinting_stdout() -> None:
    # Arrange: a non-quiet step, whose stdout the runner already painted.
    call = _shell_call("1", "echo hi", quiet=False)
    result = _Result(
        tool_results=[(call, _ToolResult(_payload("hi")))],
        final_text="done",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    # Assert: the grounded closing is shown; stdout is not reprinted under it.
    shown = "\n".join(display_chunks)
    assert "done" in shown
    assert "hi" not in shown


def test_silent_tool_turn_prints_a_blank_line() -> None:
    from core.agent_harness.turns.action_driver import _end_silent_tool_turn

    printed: list[str] = []

    class _Sink:
        def print(self, message: str = "") -> None:
            printed.append(message)

    _end_silent_tool_turn(_Sink())  # type: ignore[arg-type]

    assert printed == [""]


def test_a_generic_tool_result_is_not_replaced_by_quiet_stdout() -> None:
    # Arrange: a registry tool answers the turn while a quiet shell step probes.
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                _shell_call("2", "gh api rate_limit", quiet=True),
                _ToolResult(_payload("rate limit 4998")),
            ),
        ],
        final_text="Here is the run list.",
    )

    # Act
    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )

    # Assert: the tool's own answer stands; the probe does not displace it.
    shown = "\n".join(display_chunks)
    assert "3 failed, 59 succeeded" in shown
    assert "rate limit 4998" not in shown


def test_queued_choice_owns_the_turn_display() -> None:
    call = ToolCall(
        id="1",
        name="ask_user_choice",
        input={"title": "Deploy how?", "options": ["Canary", "Rolling"]},
    )
    result = _Result(
        tool_results=[
            (
                call,
                _ToolResult(
                    {
                        "ok": True,
                        "menu": "queued",
                        "summary": "Choose your preferred deployment strategy.",
                    }
                ),
            )
        ],
        final_text="Choose your preferred deployment strategy.",
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    assert response_text == ""
    assert display_chunks == []
    assert use_final_text is False


def test_queued_choice_preserves_sibling_tool_results() -> None:
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    choice = ToolCall(
        id="2",
        name="ask_user_choice",
        input={"title": "Deploy how?", "options": ["Canary", "Rolling"]},
    )
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                choice,
                _ToolResult(
                    {
                        "ok": True,
                        "menu": "queued",
                        "summary": "Choose your preferred deployment strategy.",
                    }
                ),
            ),
        ],
        final_text="Choose your preferred deployment strategy.",
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(2))

    assert response_text == "3 failed, 59 succeeded"
    assert display_chunks == ["3 failed, 59 succeeded"]
    assert use_final_text is False


def test_queued_choice_preserves_substantive_closing_text() -> None:
    choice = ToolCall(
        id="1",
        name="ask_user_choice",
        input={"title": "Deploy how?", "options": ["Canary", "Rolling"]},
    )
    closing = "Choose Canary only if the error rate remains stable."
    result = _Result(
        tool_results=[(choice, _ToolResult({"ok": True, "menu": "queued"}))],
        final_text=closing,
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    assert response_text == closing
    assert display_chunks == [closing]
    assert use_final_text is True


def test_queued_choice_preserves_recommendation_using_picker_words() -> None:
    choice = ToolCall(
        id="1",
        name="ask_user_choice",
        input={
            "title": "Choose a deployment environment: staging or production",
            "options": ["Staging", "Production"],
        },
    )
    closing = "Choose staging."
    result = _Result(
        tool_results=[(choice, _ToolResult({"ok": True, "menu": "queued"}))],
        final_text=closing,
    )
    session = _Session()
    session.pending_user_choice = object()

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    assert response_text == closing
    assert display_chunks == [closing]
    assert use_final_text is True


def test_choice_failure_remains_visible_with_model_closing() -> None:
    choice = ToolCall(id="1", name="ask_user_choice", input={"title": "", "options": []})
    result = _Result(
        tool_results=[
            (
                choice,
                _ToolResult(
                    {"ok": False, "error": "title is required"},
                    is_error=True,
                ),
            )
        ],
        final_text="I could not open the picker.",
    )

    response_text, display_chunks, use_final_text = _compose_response(
        result, _Session(), _counts(1)
    )

    assert response_text == "I could not open the picker.\ntitle is required"
    assert display_chunks == ["I could not open the picker.", "title is required"]
    assert use_final_text is True


def test_choice_failure_closing_preserves_self_recording_sibling_history() -> None:
    slash = ToolCall(id="1", name="slash_invoke", input={"command": "/health"})
    choice = ToolCall(id="2", name="ask_user_choice", input={"title": "", "options": []})
    result = _Result(
        tool_results=[
            (slash, _ToolResult({"ok": True})),
            (
                choice,
                _ToolResult(
                    {"ok": False, "error": "title is required"},
                    is_error=True,
                ),
            ),
        ],
        final_text="I could not open the picker.",
    )
    counts = _counts(
        2,
        executed_entries=[
            {
                "type": "slash",
                "text": "/health",
                "ok": True,
                "response_text": "Health check: degraded",
            }
        ],
    )

    response_text, display_chunks, use_final_text = _compose_response(result, _Session(), counts)

    assert response_text == (
        "Health check: degraded\nI could not open the picker.\ntitle is required"
    )
    assert display_chunks == ["I could not open the picker.", "title is required"]
    assert use_final_text is True


def test_choice_failure_remains_visible_beside_preferred_sibling_response() -> None:
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    choice = ToolCall(id="2", name="ask_user_choice", input={"title": "", "options": []})
    result = _Result(
        tool_results=[
            (github, _ToolResult(_payload("3 failed, 59 succeeded"))),
            (
                choice,
                _ToolResult(
                    {"ok": False, "error": "title is required"},
                    is_error=True,
                ),
            ),
        ],
        final_text="I could not open the picker.",
    )

    response_text, display_chunks, use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )

    assert "3 failed, 59 succeeded" in response_text
    assert "title is required" in response_text
    assert "I could not open the picker." in response_text
    assert display_chunks == [
        "I could not open the picker.",
        "3 failed, 59 succeeded\ntitle is required",
    ]
    assert use_final_text is True


def test_selected_choice_hides_only_a_pure_acknowledgement() -> None:
    result = _Result(tool_results=[], final_text="Blue-green selected.")
    session = _Session()
    session.terminal.pending_choice_response = "Blue-green"

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(0))

    assert response_text == ""
    assert display_chunks == []
    assert use_final_text is False
    assert session.terminal.pending_choice_response is None


def test_selected_choice_keeps_meaningful_follow_up_response() -> None:
    result = _Result(
        tool_results=[],
        final_text="Blue-green avoids routing a full release to every instance at once.",
    )
    session = _Session()
    session.terminal.pending_choice_response = "Blue-green"

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(0))

    shown = "\n".join(display_chunks)
    assert "Blue-green avoids routing" in shown
    assert session.terminal.pending_choice_response is None


def test_inline_tool_results_are_not_repeated_in_the_closing() -> None:
    """When the shell already nested results under ``⏺``, the closing stays the reply."""
    github = ToolCall(id="1", name="github_cli", input={"command": "api user"})
    result = _Result(
        tool_results=[(github, _ToolResult({"ok": True, "summary": "GitHub API call succeeded."}))],
        final_text="The repository is public.",
    )
    session = _Session()
    session.terminal.inline_tool_results = True
    session.terminal.collapsed_tool_output = "stashed-by-observer"

    _response_text, display_chunks, _use_final = _compose_response(result, session, _counts(1))
    shown = "\n".join(display_chunks)

    assert "The repository is public." in shown
    assert "GitHub API call succeeded." not in shown
    assert session.terminal.inline_tool_results is False
    assert session.terminal.collapsed_tool_output == "stashed-by-observer"


def test_earlier_scan_reply_does_not_replace_the_final_report() -> None:
    scan_summary = "Found 43 git repositories under /workspace."
    closing = (
        "| Metric | Result |\n| --- | --- |\n| Main branch red time | 77.0% |\n\n"
        "Would you like to schedule a reliability check?"
    )
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="scan_local_git_workspace", input={}),
                _ToolResult({**_payload(scan_summary), "rendered_in_shell": True}),
            ),
            (
                ToolCall(id="2", name="analyze_github_ci_reliability", input={}),
                _ToolResult({"ok": True, "rendered_in_shell": True}),
            ),
            (
                ToolCall(id="3", name="get_github_repository", input={}),
                _ToolResult({"ok": True, "summary": "o/r · main · internal · TypeScript"}),
            ),
        ],
        final_text=closing,
    )
    session = _Session()
    session.terminal.inline_tool_results = True

    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(3))

    assert display_chunks == [closing]
    assert closing in response_text
    assert scan_summary not in response_text
    assert use_final_text is True
    assert session.terminal.inline_tool_results is False


def test_bulky_tool_output_is_capped_and_fenced_for_display() -> None:
    # A large tool result must not flood the transcript or blend into the report:
    # it is capped and shown in its own fenced code block for the console.
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    bulky = "\n".join(f"run {i} failure 2026-08-01T09:11:00Z" for i in range(30))
    result = _Result(
        tool_results=[(github, _ToolResult(_payload(bulky)))],
        final_text="Here is the run history.",
    )

    session = _Session()
    response_text, display_chunks, _use_final = _compose_response(result, session, _counts(1))
    joined = "\n".join(display_chunks)

    # Display: capped + text-fenced (truncated content is not valid code to highlight).
    assert "```text" in joined
    assert "Ctrl+O to view" in joined
    assert sum(line.startswith("run ") for line in joined.splitlines()) <= 12
    assert "```text" not in response_text
    assert sum(line.startswith("run ") for line in response_text.splitlines()) == 30
    assert session.terminal.collapsed_tool_output == bulky


def test_truncated_json_uses_text_fence_not_json_highlight() -> None:
    """Broken mid-JSON must not paint as a dumped fence — hide the blob."""
    github = ToolCall(id="1", name="posthog_mcp", input={"tool_name": "list"})
    # Valid JSON over the line/char caps so _cap_for_display would truncate it.
    bulky_obj = {"tools": [{"name": f"tool_{i}", "description": "x" * 40} for i in range(40)]}
    bulky = json.dumps(bulky_obj, indent=2)
    result = _Result(
        tool_results=[(github, _ToolResult(_payload(bulky)))],
        final_text="Listed tools.",
    )

    _response_text, display_chunks, _use_final = _compose_response(result, _Session(), _counts(1))
    joined = "\n".join(display_chunks)

    assert "```json" not in joined
    assert "followers_url" not in joined
    assert '"tools"' not in joined
    assert "Listed tools." in joined


def test_character_and_line_truncation_markers_sit_outside_the_fence() -> None:
    github = ToolCall(id="1", name="github_cli", input={"command": "run list"})
    # Four-plus long lines so the display cap cuts the visible head *and* folds
    # remainder — the single expand marker must stay outside the fence.
    bulky = "\n".join("y" * 80 for _ in range(10))
    result = _Result(
        tool_results=[(github, _ToolResult(_payload(bulky)))],
        final_text="Here is the run history.",
    )

    _response_text, display_chunks, _use_final = _compose_response(result, _Session(), _counts(1))
    joined = "\n".join(display_chunks)

    fence_end = joined.index("```", joined.index("```text") + 1)
    after = joined[fence_end:]
    inside = joined[:fence_end]
    assert "Ctrl+O to view" in after
    assert "output truncated" not in joined
    assert after.count("Ctrl+O to view") == 1
    assert "Ctrl+O to view" not in inside


def test_plan_snapshots_are_stripped_from_the_reply() -> None:
    # The model sometimes restates the plan (or every historical snapshot) in its
    # closing text; the overlay already shows it, so display strips the snapshots
    # while keeping the prose verification.
    reply = (
        "All 12 local actions completed successfully.\n\n"
        "- Repository: /Users/x/opensre\n"
        "- Branch: perf/checks\n\n"
        "Plan · 1/7\n  ✓ Inspect path\n  ● Show branch\n  ○ Read commit\n"
        "Plan · 7/7\n  ✓ Inspect path\n  ✓ Confirm all actions succeeded (verify)"
    )
    result = _Result(tool_results=[], final_text=reply)

    _rt, display_chunks, use_final = _compose_response(result, _Session(), _counts(0))
    shown = "\n".join(display_chunks)

    assert use_final is True
    assert "All 12 local actions completed successfully." in shown
    assert "Repository: /Users/x/opensre" in shown
    assert "Plan ·" not in shown
    assert "✓ Inspect path" not in shown


def _outcome(status: str) -> str:
    return f"- **Outcome:** {status}\n- **Repository:** example/demo"


def test_repair_snapshots_collapse_to_the_latest_outcome() -> None:
    """A queued snapshot must not be reprinted next to the terminal report."""
    queued = _outcome("queued. Waiting for the scheduled tick.")
    succeeded = _outcome("succeeded. The repair commit passed CI.")
    cleanup = "Saved demo evidence and removed the scheduled repair."
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(queued)),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(succeeded)),
            ),
            (
                ToolCall(id="3", name="finish_ci_repair_demo", input={}),
                _ToolResult(_payload(cleanup)),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True

    _response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(3))
    shown = "\n".join(display_chunks)

    assert "Waiting for the scheduled tick" not in shown
    assert "The repair commit passed CI" in shown
    assert cleanup in shown
    assert shown.count("**Outcome:**") == 1
    assert use_final_text is False


def test_model_outcome_report_is_not_repeated_from_tool_snapshots() -> None:
    """The model's report is the reply; schedule and inspect snapshots stay off screen."""
    report = (
        "Repair Report\n\n"
        "- **Outcome:** Scheduled repair succeeded in one attempt on PR #1.\n"
        "- **Repair:** Loop 858332343cdc pushed ce4efbc."
    )
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
            (
                ToolCall(id="3", name="finish_ci_repair_demo", input={}),
                _ToolResult(_payload("Saved demo evidence and removed the scheduled repair.")),
            ),
        ],
        messages=[AssistantRuntimeMessage(content=report, tool_calls=())],
    )
    session = _Session()
    session.terminal.inline_tool_results = True

    _response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(3))
    shown = "\n".join(display_chunks)

    assert shown.count("Repair Report") == 1
    assert "Waiting for the scheduled tick" not in shown
    assert "The repair commit passed CI" not in shown
    assert "Saved demo evidence" not in shown
    assert use_final_text is True


def test_a_later_outcome_closing_replaces_the_earlier_report() -> None:
    """The closing report wins when an earlier tool-step report used other words."""
    earlier = "Repair Report\n\n- **Outcome:** queued. Waiting for the scheduled tick."
    later = "Repair Report\n\n- **Outcome:** succeeded. The repair commit passed CI."
    result = _Result(
        tool_results=[],
        final_text=later,
        messages=[AssistantRuntimeMessage(content=earlier, tool_calls=())],
    )

    response_text, display_chunks, use_final_text = _compose_response(
        result, _Session(), _counts(0)
    )
    shown = "\n".join(display_chunks)

    assert "Waiting for the scheduled tick" not in shown
    assert "Waiting for the scheduled tick" not in response_text
    assert "The repair commit passed CI" in shown
    assert shown.count("**Outcome:**") == 1
    assert use_final_text is True


def test_collapsing_repair_snapshots_keeps_another_tools_summary() -> None:
    """Dropping an earlier snapshot must not drop a tool that has no response text."""
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
            (
                ToolCall(id="3", name="github_cli", input={}),
                _ToolResult({"ok": True, "summary": "PR #1 is open."}),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(3))
    shown = "\n".join(display_chunks)

    assert "Waiting for the scheduled tick" not in shown
    assert "The repair commit passed CI" in shown
    assert "PR #1 is open." in shown


def test_a_short_answer_does_not_reprint_results_already_shown_inline() -> None:
    """The action log already printed the snapshots; the closing stays the answer."""
    cleanup = "Saved demo evidence and removed the scheduled repair."
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
            (
                ToolCall(id="3", name="finish_ci_repair_demo", input={}),
                _ToolResult(_payload(cleanup)),
            ),
        ],
        final_text="The repository remains.",
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(3))
    shown = "\n".join(display_chunks)

    assert shown == "The repository remains."
    assert use_final_text is True


def test_a_long_result_before_the_snapshot_does_not_hide_the_outcome() -> None:
    """The display cap keeps the report when a long result is ahead of it.

    Quiet mode stashes the action log, so this fallback is the only copy.
    """
    long_summary = "\n".join(f"log line {index}" for index in range(40))
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="github_cli", input={}),
                _ToolResult({"ok": True, "stdout": long_summary}),
            ),
            (
                ToolCall(id="2", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="3", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(3))
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert "Waiting for the scheduled tick" not in shown
    assert "log line 39" not in shown
    assert shown.count("**Outcome:**") == 1


def test_cleanup_stays_visible_when_a_long_log_precedes_it() -> None:
    """A long log must not consume the preview that the cleanup confirmation needs.

    Quiet mode stashes the action log, so this fallback is the only copy.
    """
    long_summary = "\n".join(f"log line {index}" for index in range(40))
    cleanup = "Saved demo evidence and removed the scheduled repair."
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="github_cli", input={}),
                _ToolResult({"ok": True, "stdout": long_summary}),
            ),
            (
                ToolCall(id="2", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="3", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
            (
                ToolCall(id="4", name="finish_ci_repair_demo", input={}),
                _ToolResult(_payload(cleanup)),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(4))
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert cleanup in shown
    assert "Waiting for the scheduled tick" not in shown
    assert "log line 39" not in shown
    assert shown.count("**Outcome:**") == 1


def test_a_long_outcome_report_is_capped() -> None:
    """A long reason list previews; it does not fill the terminal."""
    lines = ["- **Outcome:** succeeded. The repair commit passed CI."]
    lines.extend(f"- **Attempt {index}:** the check failed" for index in range(20))
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload("\n".join(lines))),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(2))
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert "Attempt 19" not in shown
    assert "Ctrl+O to view" in shown


def test_a_folded_report_and_log_share_one_expand_marker() -> None:
    """Two folded sections leave one Ctrl+O cue, and it stays outside the fence."""
    lines = ["- **Outcome:** succeeded. The repair commit passed CI."]
    lines.extend(f"- **Attempt {index}:** the check failed" for index in range(20))
    long_log = "\n".join(f"log line {index}" for index in range(40))
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="github_cli", input={}),
                _ToolResult({"ok": True, "stdout": long_log}),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload("\n".join(lines))),
            ),
        ]
    )

    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert "Attempt 19" not in shown
    assert "log line 39" not in shown
    fence_end = shown.index("```", shown.index("```text") + 1)
    after = shown[fence_end:]
    inside = shown[:fence_end]
    assert after.count("Ctrl+O to view") == 1
    assert "Ctrl+O to view" not in inside


def test_several_tool_previews_stay_within_one_cap() -> None:
    """Separate per-result previews must not stack into an uncapped wall."""
    tool_results = [
        (
            ToolCall(id=str(index), name="github_cli", input={}),
            _ToolResult(
                {
                    "ok": True,
                    "stdout": "\n".join(f"block {index} line {line}" for line in range(10)),
                }
            ),
        )
        for index in range(4)
    ]
    tool_results.append(
        (
            ToolCall(id="report", name="get_ci_repair_loop", input={}),
            _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
        )
    )
    _response_text, display_chunks, _use_final_text = _compose_response(
        _Result(tool_results=tool_results), _Session(), _counts(5)
    )
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert "block 3 line 9" not in shown
    assert shown.count("Ctrl+O to view") == 1


def test_a_brief_cleanup_survives_earlier_short_results() -> None:
    """Quiet mode's one cap must not cut off the trailing cleanup line."""
    cleanup = "Saved demo evidence and removed the scheduled repair."
    tool_results: list[tuple[ToolCall, _ToolResult]] = [
        (
            ToolCall(id=str(index), name="github_cli", input={}),
            _ToolResult(_payload(f"status {index}")),
        )
        for index in range(20)
    ]
    tool_results.extend(
        [
            (
                ToolCall(id="done", name="finish_ci_repair_demo", input={}),
                _ToolResult(_payload(cleanup)),
            ),
            (
                ToolCall(id="report", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(
        _Result(tool_results=tool_results), session, _counts(22)
    )
    shown = "\n".join(display_chunks)

    assert cleanup in shown
    assert "The repair commit passed CI" in shown
    assert "status 19" not in shown


def test_a_long_trailing_line_is_still_capped() -> None:
    """A one-line tail still has to honor the character cap."""
    huge = "x" * 2000
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="github_cli", input={}),
                _ToolResult(_payload(huge)),
            )
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(1))
    shown = "\n".join(display_chunks)

    assert huge not in shown
    assert "Ctrl+O to view" in shown
    assert shown.count("Ctrl+O to view") == 1


def test_one_outcome_stays_visible_after_a_long_response() -> None:
    """A single snapshot still survives when the cap would otherwise eat the tail."""
    long_text = "\n".join(f"note {index}" for index in range(40))
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="github_cli", input={}),
                _ToolResult(_payload(long_text)),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
        ]
    )
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    _response_text, display_chunks, _use_final_text = _compose_response(result, session, _counts(2))
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert "note 39" not in shown
    assert shown.count("**Outcome:**") == 1


def test_one_outcome_stays_visible_when_the_composer_is_the_only_display() -> None:
    """With no action log, the capped preview is the reply and must keep the report."""
    long_text = "\n".join(f"note {index}" for index in range(40))
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="github_cli", input={}),
                _ToolResult(_payload(long_text)),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
        ]
    )

    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(2)
    )
    shown = "\n".join(display_chunks)

    assert "The repair commit passed CI" in shown
    assert "note 39" not in shown
    assert shown.count("**Outcome:**") == 1


def test_a_short_answer_keeps_one_capped_outcome_when_results_were_not_inline() -> None:
    """Without an action log, the closing keeps the latest snapshot and caps the rest."""
    cleanup = "Saved demo evidence and removed the scheduled repair."
    long_summary = "\n".join(f"log line {index}" for index in range(40))
    result = _Result(
        tool_results=[
            (
                ToolCall(id="1", name="schedule_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("queued. Waiting for the scheduled tick."))),
            ),
            (
                ToolCall(id="2", name="get_ci_repair_loop", input={}),
                _ToolResult(_payload(_outcome("succeeded. The repair commit passed CI."))),
            ),
            (
                ToolCall(id="3", name="finish_ci_repair_demo", input={}),
                _ToolResult(_payload(cleanup)),
            ),
            (
                ToolCall(id="4", name="github_cli", input={}),
                _ToolResult({"ok": True, "stdout": long_summary}),
            ),
        ],
        final_text="The repository remains.",
    )

    _response_text, display_chunks, _use_final_text = _compose_response(
        result, _Session(), _counts(4)
    )
    shown = "\n".join(display_chunks)

    assert "The repository remains." in shown
    assert "Waiting for the scheduled tick" not in shown
    assert "The repair commit passed CI" in shown
    assert cleanup in shown
    assert "log line 0" in shown
    assert "log line 39" not in shown
    assert "Ctrl+O to view" in shown
    assert shown.count("**Outcome:**") == 1


def test_a_bullet_that_mentions_outcome_is_not_a_repair_report() -> None:
    assert is_outcome_report("- The outcome of the deploy is green.") is False
    assert is_outcome_report("- **Outcome:** succeeded.") is True


def test_tool_reply_text_is_shown_when_the_model_has_no_closing() -> None:
    card = "Scheduled: CI reliability check · o/r\nRuns weekdays at 08:00 UTC."
    call = ToolCall(id="1", name="schedule_ci_reliability_loop", input={"owner": "o", "repo": "r"})
    result = _Result(tool_results=[(call, _ToolResult(_payload(card)))])
    session = _Session()
    session.terminal.inline_tool_results = True  # type: ignore[attr-defined]

    # Act
    response_text, display_chunks, use_final_text = _compose_response(result, session, _counts(1))

    # Assert: the card is the visible closing, not an empty turn.
    assert display_chunks == [card]
    assert response_text == card
    assert use_final_text is False
