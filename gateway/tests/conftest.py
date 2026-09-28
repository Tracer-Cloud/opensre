"""Gateway pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV
from config.constants.billing import MACHINE_SECRET_ENV, USAGE_SECRET_ENV, WEBAPP_URL_ENV
from gateway.core.process import component_status, supervision


@pytest.fixture(autouse=True)
def _isolate_opensre_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every gateway test off the developer's real ``~/.opensre``.

    Patch the attribute, not ``OPENSRE_HOME``: that env var is read once at
    import, while the root helpers re-read the attribute per call.

    Disabling the keyring is not optional here. Redirecting the home makes
    credential lookup miss ``integrations.json`` and fall through to the OS
    keychain, which blocks on a GUI prompt, so the pair must move together the
    way ``tests/conftest.py`` keeps them.

    Session-end memory extraction stays off too: a recorded turn would start a
    daemon thread that calls the real LLM with the CI key from inside the test
    worker, outliving the test that recorded it.
    """
    from config.constants import paths

    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
    monkeypatch.setenv(OPENSRE_MEMORY_AUTOEXTRACT_DISABLED_ENV, "1")
    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path / "opensre-home")


@pytest.fixture(autouse=True)
def _isolate_gateway_runtime_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every gateway test off the developer's real ``~/.opensre/gateway``.

    ``GatewayController.stop()`` clears the component status file, and
    ``start_gateway`` rewrites the pidfile. Both resolve through module globals
    captured at import time, so the root ``OPENSRE_HOME_DIR`` override does not
    reach them: a unit test that merely constructs a manager and stops it
    deleted the status file of the daemon actually running on the machine,
    leaving ``opensre gateway status`` blank until the next restart.
    """
    from gateway.transports.buzz.poller import cursor as buzz_cursor

    runtime_dir = tmp_path / "gateway"
    monkeypatch.setattr(supervision, "GATEWAY_PID_FILE", runtime_dir / "gateway.pid")
    monkeypatch.setattr(supervision, "GATEWAY_LOG_FILE", runtime_dir / "gateway.log")
    monkeypatch.setattr(
        component_status, "GATEWAY_COMPONENTS_FILE", runtime_dir / "components.json"
    )
    monkeypatch.setattr(buzz_cursor, "BUZZ_CURSOR_FILE", runtime_dir / "buzz_cursor.json")


@pytest.fixture(autouse=True)
def _metering_makes_no_real_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep credit metering off the real ledger for every gateway test.

    Transport turns now consume credits, so a developer whose shell exports the
    metering env would have the test suite POST charges to a live ledger.
    Clearing these leaves every outcome ``DISABLED`` (self-hosted, no HTTP),
    and a test wanting another outcome still sets its own env or patches
    ``consume_credits``. The machine secret is cleared too so credential tests
    cannot accidentally reach Clerk through another code path.
    """
    for name in (WEBAPP_URL_ENV, USAGE_SECRET_ENV, MACHINE_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _harness_providers_per_test() -> Iterator[None]:
    """Register harness ports before each test; reset after to avoid session leakage.

    Registers the tools and integrations adapters directly (the same pair
    ``install_harness_providers`` registers) so the gateway package stays below
    ``surfaces`` in the import layering.
    """
    from infrastructure.harness_providers import reset_harness_providers
    from integrations.harness_adapters import register_harness_adapters as register_integrations
    from tools.harness_adapters import register_harness_adapters as register_tools

    register_integrations()
    register_tools()
    yield
    reset_harness_providers()
