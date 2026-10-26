"""The shell's GitHub gate: what counts as connected, and that a refusal keeps the shell closed."""

from __future__ import annotations

from typing import Any

import pytest

from config.repl_config import ReplConfig
from surfaces.interactive_shell import main as main_entrypoint
from surfaces.interactive_shell.runtime.startup import github_gate


@pytest.mark.parametrize(("verdict", "connected"), [(True, True), (False, False), (None, False)])
def test_only_a_confirmed_github_connection_opens_the_shell(
    monkeypatch: pytest.MonkeyPatch, verdict: bool | None, connected: bool
) -> None:
    """A check that cannot tell keeps the shell closed: the app would refuse the turn."""
    monkeypatch.setattr(github_gate, "resolve_integrations", lambda: {})
    monkeypatch.setattr(github_gate, "skill_prerequisite_verdict", lambda _check, _r: verdict)

    assert github_gate.github_connected() is connected


def test_without_github_the_shell_never_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """No composer and no startup menu: the REPL body is never entered."""
    started: list[Any] = []

    async def _shell(**kwargs: Any) -> int:
        started.append(kwargs)
        return 0

    monkeypatch.setattr(main_entrypoint.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main_entrypoint, "start_tool_registry_prewarm", lambda: None)
    monkeypatch.setattr(main_entrypoint, "start_integrations_prefetch", lambda: None)
    monkeypatch.setattr(main_entrypoint, "pass_sign_in_gate", lambda *_a, **_k: True)
    monkeypatch.setattr(main_entrypoint, "pass_github_gate", lambda *_a, **_k: False)
    monkeypatch.setattr(main_entrypoint, "run_repl_async", _shell)

    exit_code = main_entrypoint.run_repl(config=ReplConfig(enabled=True))

    assert exit_code == 0
    assert started == []
