from __future__ import annotations

import click
import pytest

from surfaces.shared.error_handling.errors import OpenSREError
from surfaces.shared.error_handling.exception_reporting import (
    report_exception,
    should_report_exception,
)


def test_should_not_report_unknown_command_usage_error() -> None:
    assert should_report_exception(click.UsageError("No such command 'bogus'.")) is False


def test_should_not_report_expected_cli_errors() -> None:
    assert should_report_exception(click.UsageError("No such option: --bogus")) is False
    assert should_report_exception(OpenSREError("missing integration")) is False
    assert should_report_exception(KeyboardInterrupt()) is False
    assert should_report_exception(click.Abort()) is False
    assert should_report_exception(click.ClickException("credits unavailable")) is False
    assert should_report_exception(ValueError("user input"), expected=True) is False


def test_report_exception_captures_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[BaseException, str | None]] = []

    def _capture(exc: BaseException, **kwargs: object) -> None:
        context = kwargs.get("context")
        captured.append((exc, context if isinstance(context, str) else None))

    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        _capture,
    )
    exc = RuntimeError("boom")

    assert report_exception(exc, context="test.boundary") is True
    assert captured == [(exc, "test.boundary")]


def test_report_exception_skips_expected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[BaseException] = []
    monkeypatch.setattr(
        "surfaces.shared.error_handling.exception_reporting.capture_exception",
        lambda exc, **_kwargs: captured.append(exc),
    )

    assert report_exception(OpenSREError("expected"), context="test.boundary") is False
    assert captured == []
