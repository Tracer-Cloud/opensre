"""Frozen-safe argv for the gateway daemon child."""

from __future__ import annotations

import sys

import pytest

from surfaces.shared import gateway_entrypoint as ge


def test_python_executable_uses_module_gateway_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ge.sys, "executable", sys.executable)
    monkeypatch.setattr(ge.sys, "argv", ["pytest"])
    monkeypatch.delattr(ge.sys, "frozen", raising=False)

    assert ge.gateway_entry_argv() == (
        sys.executable,
        "-m",
        "surfaces.gateway_entry",
    )


def test_frozen_binary_uses_foreground_click_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ge.sys, "executable", "/tmp/opensre")
    monkeypatch.setattr(ge.sys, "argv", ["/tmp/opensre"])
    monkeypatch.setattr(ge.sys, "frozen", True, raising=False)

    assert ge.gateway_entry_argv() == (
        "/tmp/opensre",
        "gateway",
        "start",
        "--foreground",
    )


def test_opensre_console_script_reuses_argv0_without_module_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Installed launchers may look like Python but still choke on ``-m``."""
    monkeypatch.setattr(ge.sys, "executable", "/usr/bin/python3.14")
    monkeypatch.setattr(ge.sys, "argv", ["/usr/local/bin/opensre"])
    monkeypatch.delattr(ge.sys, "frozen", raising=False)

    assert ge.gateway_entry_argv() == (
        "/usr/local/bin/opensre",
        "gateway",
        "start",
        "--foreground",
    )


def test_non_python_executable_uses_foreground_click_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ge.sys, "executable", "/opt/opensre/bin/opensre")
    monkeypatch.setattr(ge.sys, "argv", ["something-else"])
    monkeypatch.delattr(ge.sys, "frozen", raising=False)

    assert ge.gateway_entry_argv() == (
        "/opt/opensre/bin/opensre",
        "gateway",
        "start",
        "--foreground",
    )
