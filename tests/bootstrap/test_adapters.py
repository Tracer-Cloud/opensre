"""Registration steps — one implementation each, fixed order within a step.

``surfaces`` and ``gateway`` may not import each other, so each used to keep a
byte-identical copy of this registration synchronised by a docstring comment.
The composition root owns it now; the last test is what keeps a second copy from
reappearing.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from bootstrap import adapters

_REGISTRARS = {
    "integrations.harness_adapters.register_harness_adapters": "integrations",
    "tools.harness_adapters.register_harness_adapters": "tools",
}
_STEPS = ("install_harness_adapters",)


@pytest.fixture
def registration_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record which registrars ran, in order, without importing their bodies."""
    calls: list[str] = []

    def _recorder(name: str) -> Any:
        # Registrars differ in arity — the runner registrars take the runner,
        # the adapter installers take nothing — so record and ignore.
        def _record(*_args: Any, **_kwargs: Any) -> None:
            calls.append(name)

        return _record

    for target, name in _REGISTRARS.items():
        monkeypatch.setattr(target, _recorder(name))
    return calls


def test_harness_adapters_registers_both_registries(registration_calls: list[str]) -> None:
    # Arrange / Act
    adapters.install_harness_adapters()

    # Assert: both registries install; they must not silently pull in scheduler runners.
    assert registration_calls == ["integrations", "tools"]


def test_only_the_composition_root_defines_the_steps() -> None:
    # Arrange: a second copy is what this refactor removed. surfaces and gateway
    # cannot import each other, so pressure to re-add one is permanent — and a
    # comment asking people to "keep them in sync" is not enforcement.
    repo = pathlib.Path(__file__).resolve().parents[2]
    packages = (
        "bootstrap",
        "surfaces",
        "gateway",
        "tools",
        "integrations",
        "infrastructure",
        "core",
    )

    # Act
    definers = sorted(
        f"{path.relative_to(repo)}::{node.name}"
        for package in packages
        for path in (repo / package).rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name in _STEPS
    )

    # Assert
    assert definers == [
        "bootstrap/adapters.py::install_harness_adapters",
    ], definers
