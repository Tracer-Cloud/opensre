"""Socket-level guard: no wizard test may open a real outbound connection.

The onboarding wizard validates LLM credentials with a *live* provider request
(``validate_provider_credentials``, ``surfaces/shared/llm_setup/validation.py``). Every
wizard test that walks the API-key path must therefore stub that call out. The
danger is that a *mis-patched* stub fails open rather than closed:

* ``pytest.ini`` declares no network blocking and ``pytest-socket`` is not a
  dependency, so nothing else in this repo stops an outbound request.
* ``validation.py`` caches the provider SDK classes in module globals via its
  lazy ``_load_anthropic_client`` / ``_load_openai_client`` helpers. As
  ``tests/cli/wizard/test_validation.py`` already documents, those loaders can
  silently overwrite a monkeypatched client with the *real* SDK.
* ``tests/conftest.py`` loads ``.env`` with ``override=True``, and developer
  machines routinely export ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``. A real
  credential is therefore usually within reach of the test process.

Combined, a test that patches the wrong name (e.g. patching
``validation.validate_provider_credentials`` while ``flow`` holds its own
imported reference) would quietly call ``api.anthropic.com`` with a live key and
still go **green** — passing for the wrong reason and making the assertions
above it vacuous.

This fixture removes that failure mode: any attempt to open a socket during a
wizard test raises a loud ``AssertionError`` naming the fix. It is deliberately
absolute — there is no allowlist, because no test in this package has any
legitimate reason to talk to the network.
"""

from __future__ import annotations

import socket
from typing import Never

import pytest

_MESSAGE = (
    "A wizard test attempted a real outbound network connection. "
    "Wizard tests must never touch the network: stub the live provider probe with "
    'monkeypatch.setattr(flow, "validate_provider_credentials", ...) '
    "— patching the name as imported into the module under test, not the module it "
    "was defined in. See tests/cli/wizard/conftest.py."
)


class LiveNetworkCallBlocked(BaseException):
    """Raised when a wizard test tries to open a real socket.

    Deliberately derives from ``BaseException``, **not** ``Exception``.

    The provider SDKs wrap transport failures: ``anthropic`` and ``openai`` both
    catch ``Exception`` around the request and re-raise it as
    ``APIConnectionError("Connection error.")``. An ``Exception``-derived guard
    (e.g. a plain ``AssertionError``) is therefore *swallowed* by the very code
    path it exists to police, and a mis-patched test sees a tidy
    ``ValidationResult(ok=False, detail="Validation request failed: ...")``
    instead of a crash — which a failure-path test would happily accept, passing
    for exactly the wrong reason.

    Deriving from ``BaseException`` puts this outside every ``except Exception``
    in the SDK stack, so a blocked connection always surfaces as a hard test
    error that names the fix.
    """


@pytest.fixture(autouse=True)
def _no_live_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any wizard test opens a real socket."""

    def _boom(*_args: object, **_kwargs: object) -> Never:
        raise LiveNetworkCallBlocked(_MESSAGE)

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
