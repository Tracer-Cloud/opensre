from __future__ import annotations

import platform

from app.cli.__main__ import main
from app.version import get_version


def test_version_subcommand(monkeypatch, capsys) -> None:
    monkeypatch.setattr("app.cli.__main__.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("app.cli.__main__.shutdown_analytics", lambda **_kw: None)

    rc = main(["version"])
    assert rc == 0

    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert len(lines) == 3
    assert lines[0] == f"opensre {get_version()}"
    assert lines[1] == f"Python  {platform.python_version()}"
    assert lines[2] == f"OS      {platform.system().lower()} ({platform.machine()})"
