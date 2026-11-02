from __future__ import annotations

import pytest

from surfaces.cli.remote_browser import browser_is_remote


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
    assert browser_is_remote(environ, platform) is remote
