from __future__ import annotations

import pytest

from infrastructure.terminal import browser


@pytest.mark.parametrize(
    ("environ", "platform", "remote"),
    [
        ({"SSH_CONNECTION": "10.0.0.5 51234 10.0.0.9 22"}, "darwin", True),
        ({"SSH_TTY": "/dev/pts/0", "DISPLAY": "localhost:10.0"}, "linux", True),
        ({}, "linux", True),
        ({"DISPLAY": ":0"}, "linux", False),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", False),
        ({"WSL_DISTRO_NAME": "Ubuntu"}, "linux", False),
        ({}, "darwin", False),
        ({}, "win32", False),
        ({"SSH_CONNECTION": "  "}, "darwin", False),
    ],
)
def test_browser_is_remote_when_it_cannot_share_this_machines_loopback(
    environ: dict[str, str], platform: str, remote: bool
) -> None:
    assert browser.browser_is_remote(environ, platform) is remote


def test_a_remote_session_launches_nothing_and_records_the_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, object]] = []
    monkeypatch.setenv("SSH_CONNECTION", "203.0.113.7 50022 172.31.26.51 22")
    monkeypatch.setattr("webbrowser.open", lambda _url: pytest.fail("launched a browser"))
    monkeypatch.setattr(
        "infrastructure.analytics.capture.capture_browser_open_requested",
        lambda **properties: recorded.append(properties),
    )

    assert (
        browser.open_in_browser("https://app.opensre.com/home", target="integration_setup") is False
    )
    assert recorded == [
        {"target": "integration_setup", "opened": False, "cli_auth_attempt_id": None}
    ]
