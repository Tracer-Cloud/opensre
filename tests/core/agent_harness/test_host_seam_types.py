"""The host-capability seams state what they return, and cannot drift back to ``Any``.

These seams carry a capability from a host down to a tool. ``core`` calls
each factory and hands the result straight to the tool context without reading
an attribute, so the return is ``object``: honest about what ``core`` knows, and
still enough for a type checker to reject a typo.

``Any`` was the previous declaration and is what these tests exist to prevent.
It is not a smaller version of ``object`` — it switches checking off, so a
misspelled attribute on a port bundle would have compiled.
"""

from __future__ import annotations

import typing

from core.agent_harness.ports import (
    LlmFactory,
    LlmProviderPortsFactory,
    SlashPortsFactory,
    TaskCancelPortsFactory,
)
from core.llm.types import AgentLLMClient

#: The seams that hand a host capability to a tool. ``core`` forwards these.
_FORWARDED_SEAMS = {
    "LlmProviderPortsFactory": LlmProviderPortsFactory,
    "SlashPortsFactory": SlashPortsFactory,
    "TaskCancelPortsFactory": TaskCancelPortsFactory,
}


def _return_type(alias: object) -> object:
    """The return annotation of a ``Callable[[], R]`` alias."""
    args = typing.get_args(alias)
    assert args, f"expected a Callable alias, got {alias!r}"
    return args[-1]


def test_the_llm_seam_names_the_client_the_loop_actually_drives() -> None:
    """``LlmFactory`` returns ``AgentLLMClient`` — the two methods the loop calls.

    The loop calls ``invoke`` and ``tool_schemas`` and nothing else, which is
    exactly what ``AgentLLMClient`` declares. A factory returning something else
    is a wiring mistake the type checker should catch, not a runtime surprise.
    """
    # Arrange / Act
    returns = _return_type(LlmFactory)

    # Assert
    assert returns is AgentLLMClient


def test_forwarded_seams_return_object_not_any() -> None:
    """``core`` does not know these types, and ``object`` is how it says so.

    Naming the real port Protocols here would mean ``core`` importing ``tools``,
    which the layer contract forbids. ``object`` respects that and still rejects
    an attribute read; ``Any`` would silence the read as readily as the import.
    """
    # Arrange / Act
    returns = {name: _return_type(alias) for name, alias in _FORWARDED_SEAMS.items()}

    # Assert
    assert returns == dict.fromkeys(_FORWARDED_SEAMS, object)
    assert typing.Any not in returns.values()


def test_forwarded_seams_take_no_arguments() -> None:
    """Each is called with no arguments; a host binds its own state in the closure."""
    # Arrange / Act
    parameter_lists = {name: typing.get_args(alias)[0] for name, alias in _FORWARDED_SEAMS.items()}

    # Assert
    assert parameter_lists == dict.fromkeys(_FORWARDED_SEAMS, [])
