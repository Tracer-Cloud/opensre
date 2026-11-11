"""Both hosts boot with one set of capability registrars, and only they name it.

``infrastructure`` may not import ``tools`` or ``integrations``, and ``surfaces``
and ``gateway`` may not import each other, so each host names the registrars it
passes to :func:`infrastructure.process.boot.boot_process`. A comment asking
people to keep two copies in sync is not enforcement; these tests are.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator

import pytest

from infrastructure.process.boot import (
    BootStep,
    CapabilityRegistrars,
    ProcessName,
    ProcessProfile,
    boot_process,
    reset_process_runtime_for_tests,
)

_HARNESS_ONLY = ProcessProfile(
    name=ProcessName.EMBEDDED, steps=frozenset({BootStep.HARNESS_ADAPTERS})
)


@pytest.fixture(autouse=True)
def _reset_process_runtime() -> Iterator[None]:
    reset_process_runtime_for_tests()
    yield
    reset_process_runtime_for_tests()


def test_surfaces_and_gateway_boot_with_the_same_registrars() -> None:
    # Arrange / Act
    from gateway.core.process.boot import CAPABILITY_REGISTRARS as gateway_registrars
    from surfaces.shared.process_boot import CAPABILITY_REGISTRARS as surfaces_registrars

    # Assert: a registrar added to one host and not the other leaves that host's
    # processes without the capability it installs.
    assert gateway_registrars == surfaces_registrars


def test_harness_step_registers_integrations_then_tools_then_cli_auth() -> None:
    # Arrange
    calls: list[str] = []
    registrars = CapabilityRegistrars(
        integration_adapters=lambda: calls.append("integrations"),
        tool_adapters=lambda: calls.append("tools"),
        cli_auth_checker=lambda: calls.append("cli_auth"),
        scheduled_delivery=lambda: calls.append("delivery"),
    )

    # Act
    boot_process(_HARNESS_ONLY, registrars)

    # Assert: both registries install; the step must not pull in scheduled delivery.
    assert calls == ["integrations", "tools", "cli_auth"]


def test_only_the_two_hosts_name_the_registrars() -> None:
    # Arrange: a third copy is how drift starts, and nothing would compare it.
    repo = pathlib.Path(__file__).resolve().parents[3]
    packages = ("surfaces", "gateway", "tools", "integrations", "infrastructure", "core")

    # Act
    namers = sorted(
        path.relative_to(repo).as_posix()
        for package in packages
        for path in (repo / package).rglob("*.py")
        if "tests" not in path.relative_to(repo).parts
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "CapabilityRegistrars"
    )

    # Assert
    assert namers == ["gateway/core/process/boot.py", "surfaces/shared/process_boot.py"], namers
