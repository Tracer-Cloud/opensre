"""Connection lists load status in the background without blocking navigation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from surfaces.interactive_shell.session.connection_checks import ConnectionCheck
from surfaces.interactive_shell.ui import integration_browser as browser


def checked_connections(*services: str) -> dict[str, ConnectionCheck]:
    return {
        service: ConnectionCheck(
            "fixture", "passed", "Previously reachable", datetime(2026, 1, 1, tzinfo=UTC)
        )
        for service in services
    }


def run_browser(
    entries: list[browser.IntegrationEntry],
    drive: Callable[[str, Callable[[str], None]], None],
    *,
    mcp: bool = False,
    width: int = 96,
    height: int = 32,
    verify: Callable[[str], Awaitable[dict[str, str] | None]] | None = None,
    checks: dict[str, ConnectionCheck] | None = None,
) -> browser.IntegrationSelection | None:
    class Output(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=height, columns=width)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
        app = browser.build_integration_browser(entries, mcp=mcp, verify=verify, checks=checks)

        def capture(_app: Application[object]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or app.is_done:
                return
            frame = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(width))
                for y in range(screen.height)
            )
            try:
                drive(frame, pipe.send_text)
            except AssertionError as exc:
                app.exit(exception=exc)

        async def timeout() -> None:
            await asyncio.sleep(3)
            app.exit(exception=AssertionError("Browser journey did not complete"))

        app.after_render += capture
        return app.run(pre_run=lambda: app.create_background_task(timeout()))


@pytest.mark.parametrize("mcp", [False, True])
def test_search_and_inline_verification_keep_the_browser_open(mcp: bool) -> None:
    calls: list[str] = []
    stage = 0

    async def verify(service: str) -> dict[str, str]:
        calls.append(service)
        return {
            "service": service,
            "source": "local env",
            "status": "failed",
            "detail": "Datasource discovery failed: could not reach http://localhost:3000 "
            "(ConnectionError)",
        }

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        assert ("/mcp list" if mcp else "/integrations list") in frame
        if stage == 0:
            assert "Search › Filter items" in frame
            assert "Not checked" not in frame
            assert "passed" in frame and "Previously reachable" in frame and not calls
            assert "2 of 2 items" in frame
            stage = 1
            send("grafana")
        elif stage == 1 and "1 of 2 items" in frame:
            assert not calls
            stage = 2
            send("\r")
        elif stage == 2 and "ConnectionError" in frame:
            assert "failed" in frame and "local env" in frame
            assert "Search › grafana" in frame
            assert calls == ["grafana"]
            stage = 3
            send("\x1b")

    result = run_browser(
        [browser.IntegrationEntry("github", True), browser.IntegrationEntry("grafana", True)],
        drive,
        verify=verify,
        mcp=mcp,
        checks=checked_connections("github", "grafana"),
    )
    assert result is None and stage == 3


@pytest.mark.parametrize("width", [40, 96, 160])
def test_selection_does_not_probe_and_keeps_unsupported_checks_explicit(width: int) -> None:
    async def no_probe(_service: str) -> dict[str, str]:
        pytest.fail("Selection and search must not probe connections")

    def drive(frame: str, send: Callable[[str], None]) -> None:
        assert "Configured" in frame
        assert "No connectivity check available" in frame
        assert "Esc" in frame
        send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("airflow", False)], drive, width=width, verify=no_probe
        )
        is None
    )


@pytest.mark.parametrize("mcp", [False, True])
@pytest.mark.parametrize(
    "key,action", [("\x1bOQ", "setup"), ("\x1bOR", "remove"), ("\x1bOS", "add")]
)
def test_management_actions_target_the_filtered_selection(mcp: bool, key: str, action: str) -> None:
    stage = 0

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        if stage == 0:
            assert ("F3 disconnect" if mcp else "F3 remove") in frame
            stage = 1
            send("grafana")
        elif stage == 1 and "1 of 2 items" in frame:
            stage = 2
            send(key)

    result = run_browser(
        [browser.IntegrationEntry("github", True), browser.IntegrationEntry("grafana", True)],
        drive,
        mcp=mcp,
    )
    assert result == browser.IntegrationSelection(
        "setup" if action == "add" else action, None if action == "add" else "grafana"
    )


def test_empty_browser_offers_setup_but_never_removes() -> None:
    def drive(frame: str, send: Callable[[str], None]) -> None:
        assert "No MCP servers configured" in frame
        assert "F4 connect" in frame and "F3 disconnect" not in frame
        send("\r\x1bOR\x1bOQ\x1bOS")

    assert run_browser([], drive, mcp=True) == browser.IntegrationSelection("setup")


def test_unsupported_verification_is_not_offered_or_executed() -> None:
    async def no_probe(_service: str) -> dict[str, str]:
        pytest.fail("This connection has no verifier")

    def drive(frame: str, send: Callable[[str], None]) -> None:
        assert "No connectivity check available" in frame
        assert "Enter recheck" not in frame
        send("\r\x1bOQ")

    assert run_browser([browser.IntegrationEntry("airflow", False)], drive, verify=no_probe) == (
        browser.IntegrationSelection("setup", "airflow")
    )


def test_tiny_terminal_cannot_activate_hidden_actions() -> None:
    def drive(_frame: str, send: Callable[[str], None]) -> None:
        send("\x1bOR\r\x1bOS\x1b")

    assert (
        run_browser([browser.IntegrationEntry("github", True)], drive, width=20, height=6) is None
    )


def test_non_tty_never_runs_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_run(_app: Application[object]) -> None:
        pytest.fail("Non-TTY output must not read input")

    monkeypatch.setattr(
        "surfaces.shared.terminal.components.command_input.repl_tty_interactive", lambda: False
    )
    monkeypatch.setattr(Application, "run", no_run)
    assert browser.browse_integrations([browser.IntegrationEntry("github", True)]) is None


def test_verification_errors_stay_inline_and_can_be_retried() -> None:
    stage = 0
    calls = 0

    async def verify(service: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("private credential in upstream exception")
        return {"service": service, "status": "ok", "detail": "Reachable"}

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        assert "private credential" not in frame
        if stage == 0:
            stage = 1
            send("\r\r")
        elif stage == 1 and "Connectivity check failed" in frame:
            assert calls == 1
            stage = 2
            send("\r")
        elif stage == 2 and "Reachable" in frame:
            assert calls == 2 and "Connectivity check failed" not in frame
            stage = 3
            send("\x1b")

    assert run_browser([browser.IntegrationEntry("github", True)], drive, verify=verify) is None
    assert stage == 3


def test_click_changes_details_without_verifying() -> None:
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = browser.build_integration_browser(
            [browser.IntegrationEntry("github", True), browser.IntegrationEntry("grafana", True)]
        )
        controls = [
            control
            for control in app.layout.find_all_controls()
            if isinstance(control, FormattedTextControl)
        ]
        choice = next(
            control
            for control in controls
            if any("grafana" in fragment[1] for fragment in to_formatted_text(control.text))
        )
        fragment = next(
            fragment for fragment in to_formatted_text(choice.text) if "grafana" in fragment[1]
        )
        assert len(fragment) == 3
        assert app.mouse_support()
        fragment[2](MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        details = "".join(
            fragment[1] for control in controls for fragment in to_formatted_text(control.text)
        )
        assert "Full details: /integrations show grafana" in details
        assert "Full details: /integrations show github" not in details


@pytest.mark.parametrize("width", [40, 96])
def test_escape_cancels_inflight_verification(width: int) -> None:
    completed = False
    stage = 0
    previous = ConnectionCheck(
        "fixture", "passed", "Previously reachable", datetime(2026, 1, 1, tzinfo=UTC)
    )
    checks = {"github": previous}

    async def verify(_service: str) -> dict[str, str]:
        nonlocal completed
        try:
            await asyncio.Event().wait()
            raise AssertionError("Cancelled verification cannot return a result")
        finally:
            completed = True

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        if stage == 0:
            stage = 1
            send("\r")
        elif stage == 1 and "Verifying connection" in frame:
            assert "Esc close" in frame
            stage = 2
            send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True)],
            drive,
            verify=verify,
            width=width,
            checks=checks,
        )
        is None
    )
    assert stage == 2 and completed
    assert checks["github"] is previous


def test_checked_row_stays_visible_until_the_search_changes() -> None:
    stage = 0

    async def verify(service: str) -> dict[str, str]:
        return {"service": service, "status": "ok", "detail": "Reachable"}

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        if stage == 0:
            stage = 1
            send("passed")
        elif stage == 1 and "Search › passed" in frame:
            stage = 2
            send("\r")
        elif stage == 2 and "Reachable" in frame:
            assert "Search › passed" in frame
            assert "github" in frame
            stage = 3
            send("\x15impossible")
        elif stage == 3 and "0 of 2 items" in frame:
            assert "Reachable" not in frame
            stage = 4
            send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True), browser.IntegrationEntry("grafana", True)],
            drive,
            verify=verify,
            checks=checked_connections("github", "grafana"),
        )
        is None
    )
    assert stage == 4


def test_late_result_does_not_reappear_in_a_new_search() -> None:
    release = asyncio.Event()
    stage = 0

    async def verify(service: str) -> dict[str, str]:
        await release.wait()
        return {"service": service, "status": "ok", "detail": "Reachable"}

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        if stage == 0:
            stage = 1
            send("\r")
        elif stage == 1 and "Verifying connection" in frame:
            stage = 2
            send("grafana")
        elif stage == 2 and "Search › grafana" in frame:
            stage = 3
            release.set()
        elif stage == 3 and "Verifying connection" not in frame:
            assert "1 of 2 items" in frame
            assert "Full details: /integrations show grafana" in frame
            assert "github" not in frame and "Reachable" not in frame
            stage = 4
            send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True), browser.IntegrationEntry("grafana", True)],
            drive,
            verify=verify,
            checks=checked_connections("github", "grafana"),
        )
        is None
    )
    assert stage == 4


@pytest.mark.parametrize("width", [40, 96, 160])
def test_reopened_list_shows_last_check_without_probing_and_enter_refreshes(width: int) -> None:
    previous = datetime(2026, 1, 1, tzinfo=UTC)
    checks = {"github": ConnectionCheck("fixture", "passed", "Previously reachable", previous)}
    calls: list[str] = []
    stage = 0

    async def verify(service: str) -> dict[str, str]:
        calls.append(service)
        return {
            "service": service,
            "status": "failed",
            "source": "fixture",
            "detail": "Now unreachable",
            "unused_payload": "must not be cached",
        }

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        if stage == 0:
            assert "passed" in frame and "Previously reachable" in frame and not calls
            assert "Last checked:" in frame and "2026-01-01" in frame
            stage = 1
            send("\r")
        elif stage == 1 and "Now unreachable" in frame:
            assert "failed" in frame and "Last checked:" in frame
            assert calls == ["github"]
            stage = 2
            send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True)],
            drive,
            verify=verify,
            checks=checks,
            width=width,
        )
        is None
    )
    assert stage == 2
    assert checks["github"].checked_at > previous
    assert "must not be cached" not in repr(checks)

    def reopen(frame: str, send: Callable[[str], None]) -> None:
        assert "failed" in frame and "Now unreachable" in frame
        assert "Last checked:" in frame and calls == ["github"]
        send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True)],
            reopen,
            mcp=True,
            verify=verify,
            checks=checks,
            width=width,
        )
        is None
    )


def test_failed_refresh_replaces_cached_success_and_survives_reopening() -> None:
    previous = datetime(2026, 1, 1, tzinfo=UTC)
    checks = {"github": ConnectionCheck("fixture", "passed", "Previously reachable", previous)}
    calls = 0
    stage = 0

    async def verify(_service: str) -> dict[str, str]:
        nonlocal calls
        calls += 1
        raise TimeoutError("private credential in upstream exception")

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        assert "private credential" not in frame
        if stage == 0:
            assert "passed" in frame and "Previously reachable" in frame
            stage = 1
            send("\r")
        elif stage == 1 and "Connectivity check failed" in frame:
            assert "failed" in frame and "passed" not in frame
            assert "Previously reachable" not in frame and "Last checked:" in frame
            stage = 2
            send("\x1b")

    assert (
        run_browser([browser.IntegrationEntry("github", True)], drive, verify=verify, checks=checks)
        is None
    )
    assert stage == 2 and calls == 1
    assert checks["github"].status == "failed"
    assert "private credential" not in repr(checks)
    assert checks["github"].checked_at > previous

    def reopen(frame: str, send: Callable[[str], None]) -> None:
        assert "failed" in frame and "Connectivity check failed" in frame
        assert "passed" not in frame and "Previously reachable" not in frame
        assert "Last checked:" in frame and calls == 1
        send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True)],
            reopen,
            mcp=True,
            verify=verify,
            checks=checks,
        )
        is None
    )


@pytest.mark.parametrize("mcp", [False, True])
def test_fresh_list_checks_in_background_without_enter(mcp: bool) -> None:
    release = asyncio.Event()
    calls: list[str] = []
    checks: dict[str, ConnectionCheck] = {}
    stage = 0

    async def verify(service: str) -> dict[str, str]:
        calls.append(service)
        await release.wait()
        return {"service": service, "source": "fixture", "status": "passed", "detail": "Reachable"}

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        if stage == 0:
            assert "Checking…" in frame
            stage = 1
            send("github\r\r")
        elif stage == 1 and "Search › github" in frame:
            stage = 2
            release.set()
        elif stage == 2 and "Reachable" in frame:
            assert "passed" in frame and "fixture" in frame and "Last checked:" in frame
            assert calls == ["github"]
            stage = 3
            send("\x1b")

    assert (
        run_browser(
            [browser.IntegrationEntry("github", True)], drive, mcp=mcp, verify=verify, checks=checks
        )
        is None
    )
    assert stage == 3 and checks["github"].status == "passed"


@pytest.mark.parametrize("close_early", [False, True])
def test_background_checks_are_bounded_and_cancelled_on_exit(close_early: bool) -> None:
    release = asyncio.Event()
    started: list[str] = []
    stopped: list[str] = []
    active = 0
    peak = 0
    stage = 0
    checks = checked_connections("github")
    previous = checks["github"]

    async def verify(service: str) -> dict[str, str]:
        nonlocal active, peak
        started.append(service)
        active += 1
        peak = max(peak, active)
        try:
            await release.wait()
            if service == "service-0":
                raise TimeoutError("private credential in upstream exception")
            return {"service": service, "status": "passed", "detail": "Reachable"}
        finally:
            active -= 1
            stopped.append(service)

    def drive(frame: str, send: Callable[[str], None]) -> None:
        nonlocal stage
        assert "private credential" not in frame
        if stage == 0:
            assert "Checking…" in frame
            stage = 1
            send("service")
        elif stage == 1 and "Search › service" in frame:
            assert len(started) == 3
            stage = 2
            if close_early:
                send("\x1b")
            else:
                release.set()
        elif stage == 2 and not close_early and "Checking…" not in frame:
            assert "failed" in frame and "passed" in frame
            stage = 3
            send("\x1b")

    entries = [browser.IntegrationEntry(f"service-{index}", True) for index in range(6)]
    entries.extend(
        [browser.IntegrationEntry("github", True), browser.IntegrationEntry("airflow", False)]
    )
    assert run_browser(entries, drive, verify=verify, checks=checks) is None
    assert sorted(stopped) == sorted(started) and peak == 3 and active == 0
    assert checks["github"] is previous and "airflow" not in checks
    if close_early:
        assert stage == 2 and len(started) == 3 and len(checks) == 1
    else:
        assert stage == 3 and len(started) == 6 and len(checks) == 7
        assert checks["service-0"].status == "failed"
