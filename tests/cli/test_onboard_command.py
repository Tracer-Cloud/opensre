from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

onboard_module = importlib.import_module("surfaces.cli.commands.onboard")


@pytest.fixture(autouse=True)
def _stub_analytics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboard_module, "capture_onboard_started", lambda: None)
    monkeypatch.setattr(onboard_module, "capture_onboard_completed", lambda _cfg: None)
    monkeypatch.setattr(onboard_module, "capture_onboard_failed", lambda: None)


def _set_tty(monkeypatch: pytest.MonkeyPatch, *, is_tty: bool) -> None:
    monkeypatch.setattr(onboard_module.sys, "stdin", SimpleNamespace(isatty=lambda: is_tty))
    monkeypatch.setattr(onboard_module.sys, "stdout", SimpleNamespace(isatty=lambda: is_tty))


def test_onboarding_success_launches_shell_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    _set_tty(monkeypatch, is_tty=True)
    monkeypatch.delenv(onboard_module.OPENSRE_AUTO_LAUNCH_ENV, raising=False)
    monkeypatch.delenv(onboard_module.OPENSRE_PARENT_INTERACTIVE_SHELL_ENV, raising=False)
    monkeypatch.setattr(
        onboard_module, "_launch_interactive_shell", lambda _ctx: (launched.append(None), 7)[1]
    )

    with pytest.raises(SystemExit) as exc:
        onboard_module._run_onboarding_command(lambda: 0, load_config=lambda: {})

    assert exc.value.code == 7
    assert launched == [None]


def test_onboarding_success_does_not_launch_shell_when_non_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    _set_tty(monkeypatch, is_tty=False)
    monkeypatch.setattr(onboard_module, "_launch_interactive_shell", lambda: launched.append(None))

    with pytest.raises(SystemExit) as exc:
        onboard_module._run_onboarding_command(lambda: 0, load_config=lambda: {})

    assert exc.value.code == 0
    assert launched == []


def test_onboarding_success_respects_auto_launch_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[object] = []
    _set_tty(monkeypatch, is_tty=True)
    monkeypatch.setenv(onboard_module.OPENSRE_AUTO_LAUNCH_ENV, "0")
    monkeypatch.setattr(onboard_module, "_launch_interactive_shell", lambda: launched.append(None))

    with pytest.raises(SystemExit) as exc:
        onboard_module._run_onboarding_command(lambda: 0, load_config=lambda: {})

    assert exc.value.code == 0
    assert launched == []


def test_onboarding_success_does_not_launch_nested_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    _set_tty(monkeypatch, is_tty=True)
    monkeypatch.setenv(onboard_module.OPENSRE_PARENT_INTERACTIVE_SHELL_ENV, "1")
    monkeypatch.setattr(onboard_module, "_launch_interactive_shell", lambda: launched.append(None))

    with pytest.raises(SystemExit) as exc:
        onboard_module._run_onboarding_command(lambda: 0, load_config=lambda: {})

    assert exc.value.code == 0
    assert launched == []


def test_onboarding_success_respects_click_no_interactive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    ctx = SimpleNamespace(find_root=lambda: SimpleNamespace(obj={"interactive": False}))
    _set_tty(monkeypatch, is_tty=True)
    monkeypatch.setattr(onboard_module, "_launch_interactive_shell", lambda: launched.append(None))

    with pytest.raises(SystemExit) as exc:
        onboard_module._run_onboarding_command(lambda: 0, ctx=ctx, load_config=lambda: {})

    assert exc.value.code == 0
    assert launched == []


def test_onboard_auto_launch_suppresses_shell_render_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[bool] = []

    def _launch(
        _config: object,
        _resume: object,
        _after: object,
        capture_shell_rendered: bool = True,
    ) -> int:
        captured.append(capture_shell_rendered)
        return 0

    ctx = SimpleNamespace(find_root=lambda: SimpleNamespace(obj={}))
    monkeypatch.setattr(
        "surfaces.cli.host.cli_host",
        lambda _ctx: SimpleNamespace(launch_shell=_launch),
    )

    assert onboard_module._launch_interactive_shell(ctx) == 0
    assert captured == [False]
