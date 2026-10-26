"""The default agent must be able to run shell tools after process boot.

``AgentSession.start()`` is the documented Python entry point. With no
subprocess presenter, ``shell_run`` refuses every call ("subprocess presenter
is required for this action tool") and the agent silently degrades to writing
a plan it cannot execute — which reads as a reasoning failure but is a
registration gap. Observed live: the 5-step goal prompt returned PLANNED=4 /
EXECUTED=0.

The presenter cannot be a core default: it lives in ``tools`` (its process
helpers) and ``core.agent_harness`` may import neither ``tools`` nor
``gateway``. It is installed as an immutable provider at process boot instead.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import infrastructure.harness_providers as harness_providers
import infrastructure.harness_providers.subprocess_presenter as subprocess_presenter


@pytest.fixture(autouse=True)
def _restore_presenter() -> Iterator[None]:
    """Keep this module's installs out of every other test in the process."""
    previous = subprocess_presenter._installed  # noqa: SLF001
    yield
    subprocess_presenter._installed = previous  # noqa: SLF001


def test_process_boot_installs_a_subprocess_presenter_for_the_default_agent() -> None:
    """Boot leaves a presenter installed so the default port stack can execute."""
    # Arrange.
    subprocess_presenter.reset()

    # Act.
    from bootstrap.adapters import install_harness_adapters

    install_harness_adapters()

    # Assert.
    assert harness_providers.resolve_subprocess_presenter() is not None


def test_resetting_the_harness_providers_clears_the_presenter() -> None:
    """A booted presenter must not leak into tests that reset the providers.

    ``reset_harness_providers`` is the suite's "back to noop defaults" call; a
    provider it forgets stays installed for the rest of the process.
    """

    # Arrange: something installed, as after boot.
    def _presenter(*_args: object, **_kwargs: object) -> object:
        return object()

    harness_providers.SubprocessPresenterProvider(_presenter).install()

    # Act.
    harness_providers.reset_harness_providers()

    # Assert.
    assert harness_providers.resolve_subprocess_presenter() is None


def test_default_headless_build_injects_the_installed_presenter() -> None:
    """The default port stack takes the boot-installed factory at construction.

    Hosts that pass their own ``DefaultToolProvider`` keep that factory. The
    family's bare default is what ``AgentSession.start()`` uses, so it must
    receive the installed presenter or ``shell_run`` refuses every call. The
    provider itself must not look the factory up — a missing constructor arg
    stays missing even when one is installed.
    """
    # Arrange.
    from core.agent_harness.session import SessionCore
    from core.agent_harness.session.persistence.memory import InMemorySessionStore
    from core.agent_harness.tools.tool_provider import DefaultToolProvider
    from core.agent_harness.turns.headless_adapters import BufferOutputSink
    from core.agent_harness.turns.headless_build import DefaultHeadlessBuild

    def _installed(*_args: object, **_kwargs: object) -> object:
        return object()

    def _explicit(*_args: object, **_kwargs: object) -> object:
        return object()

    harness_providers.SubprocessPresenterProvider(_installed).install()
    with_explicit = DefaultToolProvider(object(), object(), subprocess_presenter_factory=_explicit)
    without = DefaultToolProvider(object(), object())
    default_tools = DefaultHeadlessBuild(
        session=SessionCore(store=InMemorySessionStore()),
        output=BufferOutputSink(),
    ).tools()

    # Act / Assert.
    assert with_explicit._subprocess_presenter_factory is _explicit  # noqa: SLF001
    assert without._subprocess_presenter_factory is None  # noqa: SLF001
    assert isinstance(default_tools, DefaultToolProvider)
    assert default_tools._subprocess_presenter_factory is _installed  # noqa: SLF001
