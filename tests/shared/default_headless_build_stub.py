"""Test stand-in for ``DefaultHeadlessBuild`` that routes construction through one ``build`` callable."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def default_headless_build_stub(build: Callable[..., Any]) -> type:
    """Return a ``DefaultHeadlessBuild`` stand-in whose ``agent(**agent_kwargs)`` calls ``build(**fields, **agent_kwargs)``.

    Patch it over ``<module>.DefaultHeadlessBuild`` so a test that used to fake the
    agent factory keeps one hook: ``build`` sees the build's fields
    (``session``, ``output``, ``console``, ``logger``, ``surface``) merged
    with the tools/prompts the host passed to ``agent()`` (``tools``, ``prompts``,
    ``deps``).
    """

    class _DefaultHeadlessBuildStub:
        def __init__(self, **fields: Any) -> None:
            self._fields = fields

        def agent(self, **agent_kwargs: Any) -> Any:
            return build(**self._fields, **agent_kwargs)

    return _DefaultHeadlessBuildStub


__all__ = ["default_headless_build_stub"]
