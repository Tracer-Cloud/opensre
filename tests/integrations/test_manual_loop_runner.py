"""Tests for the manual loop runner's deterministic report builders."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from integrations import manual_loop_runner
from integrations.github.tools.ci_analytics import loop as ci_loop


def _no_model_turn(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a loop with a report builder must not run a model turn")


def test_loop_naming_a_builder_runs_it_instead_of_a_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the registry points at the CI reliability builder; fake it.
    received: list[Mapping[str, str]] = []

    def fake_build_report(args: Mapping[str, str]) -> str:
        received.append(dict(args))
        return "**CI/CD reliability for o/r, last 7 days**"

    monkeypatch.setattr(ci_loop, "build_report", fake_build_report)
    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", _no_model_turn)
    payload = {
        "loop_prompt": "fallback prompt",
        "name": "CI reliability check",
        "loop_report": "github_ci_reliability",
        "loop_report_args": json.dumps({"owner": "o", "repo": "r", "days": "7"}),
    }

    # Act
    report = manual_loop_runner.run_manual_prompt_loop(payload)

    # Assert
    assert report.startswith("**CI/CD reliability for o/r")
    assert received == [{"owner": "o", "repo": "r", "days": "7"}]


def test_loop_without_a_builder_still_runs_the_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Result:
        answered = True
        primary_response_text = "report body"

    def fake_turn(message: str, **_kwargs: object) -> _Result:
        calls.append(message)
        return _Result()

    monkeypatch.setattr(manual_loop_runner.AgentSession, "run_headless_turn", fake_turn)

    report = manual_loop_runner.run_manual_prompt_loop(
        {"loop_prompt": "Summarize stars", "name": "x"}
    )

    assert report == "report body"
    assert "Summarize stars" in calls[0]


def test_unknown_builder_name_falls_back_to_the_model_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        answered = True
        primary_response_text = "fallback"

    monkeypatch.setattr(
        manual_loop_runner.AgentSession, "run_headless_turn", lambda *_a, **_k: _Result()
    )

    report = manual_loop_runner.run_manual_prompt_loop(
        {"loop_prompt": "p", "name": "x", "loop_report": "not-a-builder"}
    )

    assert report == "fallback"
