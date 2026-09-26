"""Bridge Click global flags into :mod:`infrastructure.process.runtime_flags`."""

from __future__ import annotations

import click

from infrastructure.process.runtime_flags import configure_runtime_flags


def sync_runtime_flags_from_click(ctx: click.Context | None = None) -> None:
    """Copy root Click context flags into the shared runtime flag store."""
    current = ctx if ctx is not None else click.get_current_context(silent=True)
    if current is None:
        return
    root = current
    while root.parent is not None:
        root = root.parent
    obj = root.obj or {}
    configure_runtime_flags(
        json=bool(obj.get("json")),
        verbose=bool(obj.get("verbose")),
        debug=bool(obj.get("debug")),
        yes=bool(obj.get("yes")),
        onboarding=not bool(obj.get("skip_onboarding")),
        interactive=bool(obj.get("interactive", True)),
    )
