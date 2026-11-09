"""Tests for boot-time sandbox capability probes."""

from __future__ import annotations

from typing import Any

from config.constants.runtime_metadata import OPENSRE_ALLOW_NETWORK_ENV
from infrastructure.safety.sandbox.capabilities import (
    Capability,
    _file_grep_available,
    _file_read_available,
    _network_available,
    _python_available,
    _shell_available,
    boot_capability_warnings,
    probe_capabilities,
    unavailable_capability_warnings,
)


def test_python_execution_is_detected() -> None:
    # Arrange / Act
    results = probe_capabilities()

    # Assert
    assert results[Capability.PYTHON].available is True


def test_python_probe_requires_successful_execution(monkeypatch: Any) -> None:
    def _failed_run(_code: str, **_kwargs: Any) -> Any:
        return type(
            "R",
            (),
            {
                "success": False,
                "stdout": "2\n",
                "stderr": "",
                "error": "Python interpreter is unavailable",
            },
        )()

    monkeypatch.setattr(
        "infrastructure.safety.sandbox.runner.run_python_sandbox",
        _failed_run,
    )

    assert _python_available() is False


def test_file_read_is_detected() -> None:
    results = probe_capabilities()
    assert results[Capability.FILE_READ].available is True


def test_shell_probe_reports_missing_interpreters(monkeypatch: Any) -> None:
    # Arrange
    probed: list[str] = []

    def _which(name: str) -> None:
        probed.append(name)

    monkeypatch.setattr("infrastructure.safety.sandbox.capabilities.shutil.which", _which)

    # Act
    available = _shell_available()

    # Assert
    assert available is False
    assert probed == ["bash", "sh"]


def test_file_grep_probe_reports_missing_executable(monkeypatch: Any) -> None:
    # Arrange
    probed: list[str] = []

    def _which(name: str) -> None:
        probed.append(name)

    monkeypatch.setattr("infrastructure.safety.sandbox.capabilities.shutil.which", _which)

    # Act
    available = _file_grep_available()

    # Assert
    assert available is False
    assert probed == ["grep"]


def test_every_capability_is_reported() -> None:
    """A missing key would silently drop a capability from the warning list."""
    results = probe_capabilities()
    assert set(results) == set(Capability)


def test_warnings_name_only_the_unavailable_ones() -> None:
    # Arrange: one capability reported unavailable.
    results = probe_capabilities()
    results[Capability.NETWORK] = results[Capability.NETWORK].__class__(
        capability=Capability.NETWORK, available=False, detail="blocked by policy"
    )

    # Act
    warnings = unavailable_capability_warnings(results)

    # Assert
    assert len(warnings) == 1
    assert "network" in warnings[0].lower()
    assert "blocked by policy" in warnings[0]
    assert "configured integration" in warnings[0]


def test_no_warnings_when_everything_works() -> None:
    results = probe_capabilities()
    for name in list(results):
        results[name] = results[name].__class__(capability=name, available=True, detail="")
    assert unavailable_capability_warnings(results) == []


def test_probe_never_raises_even_when_the_check_explodes(monkeypatch: Any) -> None:
    """A diagnostic must not be able to take down startup."""

    # Arrange
    def _boom(*_args: Any, **_kwargs: Any) -> bool:
        raise OSError("environment is hostile")

    monkeypatch.setattr("infrastructure.safety.sandbox.capabilities._python_available", _boom)

    # Act
    results = probe_capabilities()

    # Assert: reported unavailable with the reason, not propagated.
    assert results[Capability.PYTHON].available is False
    assert "hostile" in results[Capability.PYTHON].detail


def test_network_probe_does_not_make_a_real_request(monkeypatch: Any) -> None:
    """Startup must not depend on, or wait for, an external host."""
    # Arrange
    import socket

    def _fail(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("capability probe attempted a real connection")

    monkeypatch.setattr(socket, "create_connection", _fail)

    # Act / Assert — no exception means no outbound call was attempted.
    probe_capabilities()


def test_network_probe_uses_default_sandbox_policy(monkeypatch: Any) -> None:
    """Do not pass allow_network=True — that bypasses the normal sandbox block."""
    calls: list[dict[str, Any]] = []

    def _fake_run(code: str, **kwargs: Any) -> Any:
        calls.append({"code": code, **kwargs})
        return type(
            "R",
            (),
            {
                "success": False,
                "stdout": "",
                "stderr": "PermissionError: Network access is not permitted",
            },
        )()

    monkeypatch.setattr("infrastructure.safety.sandbox.runner.run_python_sandbox", _fake_run)
    assert _network_available() is False
    assert calls and calls[0].get("allow_network", False) is False
    assert "socket.socket" in calls[0]["code"]


def test_network_probe_is_unavailable_when_sandbox_cannot_start(monkeypatch: Any) -> None:
    def _failed_run(_code: str, **_kwargs: Any) -> Any:
        return type(
            "R",
            (),
            {
                "success": False,
                "stdout": "",
                "stderr": "",
                "error": "Python interpreter is unavailable",
            },
        )()

    monkeypatch.setattr(
        "infrastructure.safety.sandbox.runner.run_python_sandbox",
        _failed_run,
    )

    assert _network_available() is False


def test_boot_capability_warnings_merges_path_and_sandbox(monkeypatch: Any) -> None:
    seen_tools: list[Any] = []

    def _facts(tools: Any = None) -> dict[str, list[str]]:
        seen_tools.append(tools)
        return {"capability_warnings": ["curl is not on PATH", "dup"]}

    monkeypatch.setattr(
        "config.runtime_metadata.probes.capability_warning_facts",
        _facts,
    )
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.unavailable_capability_warnings",
        lambda _results=None: ["dup", "network requests is unavailable"],
    )

    assert boot_capability_warnings() == [
        "curl is not on PATH",
        "dup",
        "network requests is unavailable",
    ]
    assert boot_capability_warnings(
        installed_tools={"curl": "", "bash": "/bin/bash"},
    ) == [
        "curl is not on PATH",
        "dup",
        "network requests is unavailable",
    ]
    assert seen_tools[-1] == {"curl": "", "bash": "/bin/bash"}
    assert boot_capability_warnings(include_path_facts=False) == [
        "dup",
        "network requests is unavailable",
    ]


def test_boot_warnings_make_every_basic_capability_actionable(
    monkeypatch: Any,
) -> None:
    # Arrange
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    monkeypatch.setattr(
        "infrastructure.safety.sandbox.capabilities.unavailable_capability_warnings",
        lambda _results=None: [],
    )
    installed_tools = {
        "bash": "",
        "curl": "",
        "grep": "",
        "python": "",
        "python3": "",
        "sh": "",
    }

    # Act
    warnings = boot_capability_warnings(installed_tools=installed_tools)
    rendered = "\n".join(warnings).lower()

    # Assert
    assert "install curl" in rendered
    assert "install bash or sh" in rendered
    assert "install grep" in rendered
    assert "configured integration" in rendered
    assert "install python 3" in rendered


def test_file_read_available_when_cwd_is_empty(tmp_path: Any, monkeypatch: Any) -> None:
    """An empty working tree is still readable — do not require a child entry."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    assert _file_read_available() is True
