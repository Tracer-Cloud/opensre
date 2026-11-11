"""Wire tools-layer helpers into :mod:`infrastructure.harness_providers`."""

from __future__ import annotations


def register_harness_adapters() -> None:
    from infrastructure.harness_providers import SubprocessPresenterProvider, ToolSources
    from tools.interactive_shell.skill_scripts import registered_skill_tools
    from tools.interactive_shell.subprocess_presenter import (
        headless_subprocess_presenter_factory,
    )
    from tools.registry import RegisteredToolRegistry

    ToolSources(registry=RegisteredToolRegistry(), skill_tools=registered_skill_tools).install()
    # Without a presenter ``shell_run`` refuses every call, and the default agent
    # plans work it cannot execute.
    SubprocessPresenterProvider(headless_subprocess_presenter_factory).install()
