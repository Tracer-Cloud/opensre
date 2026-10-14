"""Headless/SSH behavior for the account login browser handoff (issue #6719)."""

from __future__ import annotations

import http.client
import threading
from io import StringIO
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from rich.console import Console

from surfaces.cli import account_auth
from surfaces.cli.account_auth import AccountLoginResult
from surfaces.cli.account_ui import AccountLoginPresenter


def _fake_exchange(*_args: Any, **_kwargs: Any) -> account_auth._ExchangeResult:
    return account_auth._ExchangeResult(
        access_token="osre_pat_secret",
        token_expires_at="2026-12-01T10:00:00+00:00",
        user_id="user_123",
        organization_id="org_123",
        llm_provider="openai",
        llm_model="gpt-5.4-mini",
        email="octocat@example.com",
    )


class _RecordingProgress:
    def __init__(self, on_prompt: Any = None) -> None:
        self.urls: list[str] = []
        self.opened: list[bool] = []
        self.tunnel_hints: list[str] = []
        self._on_prompt = on_prompt

    def prompt_sign_in(self, url: str, *, opened: bool) -> None:
        self.urls.append(url)
        self.opened.append(opened)
        if self._on_prompt is not None:
            self._on_prompt(url)

    def ssh_tunnel_hint(self, command: str) -> None:
        self.tunnel_hints.append(command)

    def authorization_received(self) -> None:
        return

    def setup_complete(self) -> None:
        return


def _capture_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _stub_account_persistence(monkeypatch: pytest.MonkeyPatch, result: _RecordingProgress):
    _ = result
    monkeypatch.setattr(account_auth, "_exchange_code", _fake_exchange)
    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "")
    monkeypatch.setattr(account_auth, "save_account_token", lambda _token: None)
    monkeypatch.setattr(account_auth, "save_account_record", lambda _record: None)
    monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)


@pytest.mark.parametrize(
    ("environ", "platform", "expected"),
    [
        ({"SSH_CONNECTION": "203.0.113.7 51000 198.51.100.4 22"}, "linux", True),
        ({"SSH_CONNECTION": "203.0.113.7 51000 198.51.100.4 22", "DISPLAY": ":0"}, "linux", True),
        ({"SSH_TTY": "/dev/pts/3"}, "linux", True),
        ({"TMUX": "/tmp/tmux-1000/default,123,0"}, "linux", True),
        ({"TMUX": "/tmp/tmux-1000/default,123,0", "DISPLAY": ":0"}, "linux", False),
        ({"TMUX": "/tmp/tmux-1000/default,123,0"}, "darwin", False),
        ({}, "linux", False),
        ({"DISPLAY": ":0"}, "linux", False),
        ({"SSH_CONNECTION": "", "TMUX": ""}, "linux", False),
    ],
)
def test_headless_detection_matrix(environ: dict[str, str], platform: str, expected: bool) -> None:
    assert account_auth._headless_environment(environ=environ, platform=platform) is expected


def test_ssh_session_skips_the_browser_and_prints_a_tunnel_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.7 51000 198.51.100.4 22")
    monkeypatch.setattr(account_auth.getpass, "getuser", lambda: "octocat")
    monkeypatch.delenv("SSH_TTY", raising=False)
    opened_urls: list[str] = []

    def spy_browser_open(url: str) -> bool:
        opened_urls.append(url)
        return True

    monkeypatch.setattr(
        account_auth,
        "_wait_for_callback",
        lambda *_args, **_kwargs: account_auth._CallbackResult(code="c"),
    )
    _stub_account_persistence(monkeypatch, None)
    progress = _RecordingProgress()

    result: AccountLoginResult = account_auth.login_account(
        app_url="https://app.opensre.com",
        open_browser=True,
        browser_open=spy_browser_open,
        progress=progress,
    )

    assert result.record.email == "octocat@example.com"
    assert opened_urls == [], "a browser must not be opened over SSH"
    assert progress.opened == [False]
    query = parse_qs(urlsplit(progress.urls[0]).query)
    callback_port = query["callback_port"][0]
    assert len(progress.tunnel_hints) == 1
    hint = progress.tunnel_hints[0]
    assert "ssh -L" in hint
    assert f"{callback_port}:127.0.0.1:{callback_port}" in hint
    assert "octocat@198.51.100.4" in hint


def test_ssh_login_completes_through_the_real_loopback_without_a_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.7 51000 198.51.100.4 22")
    opened_urls: list[str] = []

    def spy_browser_open(url: str) -> bool:
        opened_urls.append(url)
        return True

    exchanged: dict[str, str] = {}

    def exchange(app_url: str, code: str, verifier: str) -> account_auth._ExchangeResult:
        exchanged.update(app_url=app_url, code=code)
        return _fake_exchange()

    monkeypatch.setattr(account_auth, "_exchange_code", exchange)
    monkeypatch.setattr(account_auth, "load_account_record", lambda: None)
    monkeypatch.setattr(account_auth, "stored_account_token", lambda: "")
    monkeypatch.setattr(account_auth, "save_account_token", lambda _token: None)
    monkeypatch.setattr(account_auth, "save_account_record", lambda _record: None)
    monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)

    def hit_callback(url: str) -> None:
        query = parse_qs(urlsplit(url).query)
        port = int(query["callback_port"][0])
        state = query["state"][0]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", f"/callback?code=osre_code_one_time&state={state}")
            conn.getresponse().read()
        finally:
            conn.close()

    progress = _RecordingProgress(
        on_prompt=lambda url: threading.Thread(
            target=hit_callback, args=(url,), daemon=True
        ).start()
    )

    result = account_auth.login_account(
        app_url="https://app.opensre.com",
        open_browser=True,
        browser_open=spy_browser_open,
        timeout_seconds=15.0,
        progress=progress,
    )

    assert opened_urls == [], "a browser must not be opened over SSH"
    assert exchanged["code"] == "osre_code_one_time"
    assert result.record.email == "octocat@example.com"
    assert progress.tunnel_hints, "the SSH session must receive tunnel instructions"


def test_browser_open_failure_degrades_to_the_printed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("SSH_CONNECTION", "SSH_TTY", "TMUX"):
        monkeypatch.delenv(var, raising=False)

    def broken_browser_open(_url: str) -> bool:
        raise RuntimeError("no browser on this box")

    monkeypatch.setattr(
        account_auth,
        "_wait_for_callback",
        lambda *_args, **_kwargs: account_auth._CallbackResult(code="c"),
    )
    _stub_account_persistence(monkeypatch, None)
    progress = _RecordingProgress()

    result = account_auth.login_account(
        app_url="https://app.opensre.com",
        open_browser=True,
        browser_open=broken_browser_open,
        progress=progress,
    )

    assert result.record.email == "octocat@example.com"
    assert progress.opened == [False]
    assert progress.urls, "the sign-in URL is still printed for manual opening"
    assert progress.tunnel_hints == [], "no SSH, no tunnel hint"


def test_login_presenter_prints_the_ssh_tunnel_hint() -> None:
    console, buf = _capture_console()
    presenter = AccountLoginPresenter(console)

    presenter.prompt_sign_in(
        "https://app.opensre.com/cli/auth/start?callback_port=43721", opened=False
    )
    presenter.ssh_tunnel_hint("ssh -L 43721:127.0.0.1:43721 octocat@198.51.100.4")

    output = buf.getvalue()
    assert "Open this link in your browser" in output
    assert "ssh -L 43721:127.0.0.1:43721 octocat@198.51.100.4" in output
