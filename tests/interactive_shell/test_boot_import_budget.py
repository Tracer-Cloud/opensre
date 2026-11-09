"""Boot-time import-budget guardrails for the interactive shell.

These lock in the lazy-loading wins so a future eager import in the boot path
(controller construction, banner, background workers, prompt/completion) fails
loudly instead of silently re-inflating REPL startup.
"""

from __future__ import annotations

import subprocess
import sys

# Modules that must NOT be pulled into the graph merely by importing the REPL
# entrypoint. Each maps to the lazy-loading change that keeps it out.
_FORBIDDEN_AT_BOOT: tuple[str, ...] = (
    # Harness/turn-execution stack — deferred until the first turn is dispatched.
    "surfaces.interactive_shell.runtime.shell_turn_execution",
    "core.agent.agent",
    "core.agent_harness.turns.action_driver",
    # Email delivery — only loads on background-RCA completion.
    "integrations.smtp.delivery",
)


def _modules_loaded_by(import_target: str, candidates: tuple[str, ...]) -> list[str]:
    check = "; ".join(f"print('{name}', '{name}' in sys.modules)" for name in candidates)
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; import {import_target}; {check}"],
        check=True,
        capture_output=True,
        text=True,
    )
    loaded: list[str] = []
    for line in result.stdout.splitlines():
        name, _, flag = line.partition(" ")
        if flag.strip() == "True":
            loaded.append(name)
    return loaded


def test_repl_boot_import_stays_lazy() -> None:
    loaded = _modules_loaded_by(
        "surfaces.interactive_shell.main",
        _FORBIDDEN_AT_BOOT,
    )
    assert loaded == [], (
        "importing surfaces.interactive_shell.main eagerly pulled modules that "
        f"must load lazily: {loaded}"
    )


def test_loading_the_tool_registry_leaves_the_kubernetes_sdk_unloaded() -> None:
    """The first menu waits for the registry; the EKS and Kubernetes tools put the SDK (~0.7s) on it."""
    check = (
        "import sys; from surfaces.cli.app import cli; from surfaces.cli import startup; "
        "startup.run(cli, []); "
        "from core.agent_harness.tools import registered_single_turn_tool_names; "
        "registered_single_turn_tool_names(); "
        "print('kubernetes' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", check], check=True, capture_output=True, text=True
    )

    assert result.stdout.split()[-1] == "False"
