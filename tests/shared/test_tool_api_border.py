"""Consumers import the tool packages through their public API, and the surface only shrinks.

``core/tool/`` (contract, execution, registry port) and ``core/tool_framework/``
(``@tool``, skill guidance, payload utilities) are one group to everything above
them. Three public API modules are the allowed way in: ``core.tool`` (the
contract — what a tool is, how it runs, where it is registered),
``core.tool_framework`` (the ``@tool`` decorator, planning tags, skill guidance),
and ``core.tool_framework.utils`` (schema builders, MCP readers, availability
envelopes). Any import reaching past those is listed below, and these allowlists
measure how far off that rule the code still is.

Every consumer is now at zero: they use the public API modules and nothing behind them.

Each allowlist is compared exactly in both directions: a new internal import
fails immediately, and an entry no longer imported must be removed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.shared.tool_api import TOOL_BORDER

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Internal tool-package modules each consumer still imports directly.
_ALLOWED: dict[str, frozenset[str]] = {
    "tools": frozenset(),
    "integrations": frozenset(),
    "gateway": frozenset(),
    "surfaces": frozenset(),
    "infrastructure": frozenset(),
}


@pytest.mark.parametrize("consumer", sorted(_ALLOWED))
def test_consumer_tool_package_imports_match_the_allowlist(consumer: str) -> None:
    # Arrange / Act
    imported = TOOL_BORDER.internal_imports_under(
        REPO_ROOT / consumer, exclude_parts=frozenset({"tests"})
    )

    # Assert
    TOOL_BORDER.assert_matches_allowlist(imported, _ALLOWED[consumer], consumer=f"{consumer}/")


def test_bootstrap_reaches_the_tool_packages_only_through_their_api() -> None:
    """The composition root wires tools together; it must not open these packages up."""
    # Arrange / Act
    imported = TOOL_BORDER.internal_imports_under(REPO_ROOT / "bootstrap")

    # Assert
    assert imported == set()


def test_the_contract_api_survives_being_imported_second() -> None:
    """``import core.llm.types`` must not re-enter a half-built ``core.tool``.

    ``core.tool.execution`` needs ``ToolCall`` from ``core.llm.types``, which in
    turn names ``RuntimeTool`` as a PEP 695 bound. Once ``core/tool/__init__``
    imports execution, a runtime import of that bound makes importing
    ``core.llm.types`` first fail with ``cannot import name 'ToolCall' from
    partially initialized module``. The bound is lazy, so the import stays under
    ``TYPE_CHECKING`` — and this test is what says so out loud.
    """
    # Arrange: a fresh interpreter, importing the risky order first.
    script = "import core.llm.types, core.tool; print(len(core.tool.__all__))"

    # Act
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    # Assert
    assert proc.returncode == 0, proc.stderr.strip()[-400:]
    assert proc.stdout.strip() == "17"
