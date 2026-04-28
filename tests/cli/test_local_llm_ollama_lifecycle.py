"""Direct unit tests for Ollama lifecycle helpers: is_installed, start_server, wait_for_server, install."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import questionary

from app.cli.local_llm.ollama import (
    install,
    is_installed,
    start_server,
    wait_for_server,
)

# ---------------------------------------------------------------------------
# is_installed
# ---------------------------------------------------------------------------


def test_is_installed_returns_true_when_ollama_on_path(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.shutil.which", lambda _: "/usr/local/bin/ollama")
    assert is_installed() is True


def test_is_installed_returns_false_when_ollama_not_on_path(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.shutil.which", lambda _: None)
    assert is_installed() is False


# ---------------------------------------------------------------------------
# start_server
# ---------------------------------------------------------------------------


def test_start_server_opens_popen_with_ollama_serve(monkeypatch) -> None:
    fake_proc = MagicMock(spec=subprocess.Popen)
    popen_calls: list[tuple[list[str], dict]] = []

    def fake_popen(args, **kwargs):  # type: ignore[no-untyped-def]
        popen_calls.append((args, kwargs))
        return fake_proc

    monkeypatch.setattr("app.cli.local_llm.ollama.subprocess.Popen", fake_popen)
    result = start_server()

    assert result is fake_proc
    assert len(popen_calls) == 1
    args, kwargs = popen_calls[0]
    assert args == ["ollama", "serve"]
    assert kwargs.get("stdout") == subprocess.DEVNULL
    assert kwargs.get("stderr") == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# wait_for_server
# ---------------------------------------------------------------------------


def test_wait_for_server_returns_true_on_first_attempt(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.is_server_running", lambda _: True)
    monkeypatch.setattr("app.cli.local_llm.ollama.time.sleep", lambda _: None)
    assert wait_for_server("http://localhost:11434") is True


def test_wait_for_server_returns_true_after_retries(monkeypatch) -> None:
    attempt: dict[str, int] = {"count": 0}

    def flaky_check(_host: str) -> bool:
        attempt["count"] += 1
        return attempt["count"] >= 3  # fails twice, then succeeds

    monkeypatch.setattr("app.cli.local_llm.ollama.is_server_running", flaky_check)
    monkeypatch.setattr("app.cli.local_llm.ollama.time.sleep", lambda _: None)

    assert wait_for_server("http://localhost:11434", timeout_s=5) is True
    assert attempt["count"] == 3


def test_wait_for_server_returns_false_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.is_server_running", lambda _: False)
    sleep_calls: list[float] = []
    monkeypatch.setattr("app.cli.local_llm.ollama.time.sleep", lambda s: sleep_calls.append(s))

    assert wait_for_server("http://localhost:11434", timeout_s=3) is False
    assert len(sleep_calls) == 3


# ---------------------------------------------------------------------------
# install — macOS with Homebrew present
# ---------------------------------------------------------------------------


def test_install_macos_brew_present_user_confirms_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "darwin")
    monkeypatch.setattr("app.cli.local_llm.ollama.shutil.which", lambda _: "/usr/local/bin/brew")
    fake_result = MagicMock()
    fake_result.returncode = 0
    monkeypatch.setattr("app.cli.local_llm.ollama.subprocess.run", lambda *_a, **_kw: fake_result)
    monkeypatch.setattr(questionary, "confirm", lambda *_a, **_kw: MagicMock(ask=lambda: True))

    console = MagicMock()
    assert install(console) is True


def test_install_macos_brew_present_user_declines_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "darwin")
    monkeypatch.setattr("app.cli.local_llm.ollama.shutil.which", lambda _: "/usr/local/bin/brew")
    monkeypatch.setattr(questionary, "confirm", lambda *_a, **_kw: MagicMock(ask=lambda: False))

    console = MagicMock()
    assert install(console) is False


def test_install_macos_brew_absent_returns_false_and_prints_link(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "darwin")
    monkeypatch.setattr("app.cli.local_llm.ollama.shutil.which", lambda _: None)

    console = MagicMock()
    assert install(console) is False
    # Should print both the "no homebrew" warning and the download URL
    assert console.print.call_count >= 2


def test_install_macos_brew_present_subprocess_fails_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "darwin")
    monkeypatch.setattr("app.cli.local_llm.ollama.shutil.which", lambda _: "/usr/local/bin/brew")
    fake_result = MagicMock()
    fake_result.returncode = 1
    monkeypatch.setattr("app.cli.local_llm.ollama.subprocess.run", lambda *_a, **_kw: fake_result)
    monkeypatch.setattr(questionary, "confirm", lambda *_a, **_kw: MagicMock(ask=lambda: True))

    console = MagicMock()
    assert install(console) is False


# ---------------------------------------------------------------------------
# install — Linux
# ---------------------------------------------------------------------------


def test_install_linux_user_confirms_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "linux")
    fake_result = MagicMock()
    fake_result.returncode = 0
    monkeypatch.setattr("app.cli.local_llm.ollama.subprocess.run", lambda *_a, **_kw: fake_result)
    monkeypatch.setattr(questionary, "confirm", lambda *_a, **_kw: MagicMock(ask=lambda: True))

    console = MagicMock()
    assert install(console) is True


def test_install_linux_user_declines_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "linux")
    monkeypatch.setattr(questionary, "confirm", lambda *_a, **_kw: MagicMock(ask=lambda: False))

    console = MagicMock()
    assert install(console) is False


def test_install_linux_subprocess_fails_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "linux")
    fake_result = MagicMock()
    fake_result.returncode = 2
    monkeypatch.setattr("app.cli.local_llm.ollama.subprocess.run", lambda *_a, **_kw: fake_result)
    monkeypatch.setattr(questionary, "confirm", lambda *_a, **_kw: MagicMock(ask=lambda: True))

    console = MagicMock()
    assert install(console) is False


# ---------------------------------------------------------------------------
# install — Windows (unsupported)
# ---------------------------------------------------------------------------


def test_install_windows_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("app.cli.local_llm.ollama.sys.platform", "win32")

    console = MagicMock()
    assert install(console) is False
    console.print.assert_called()
