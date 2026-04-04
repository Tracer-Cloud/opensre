"""Root pytest configuration — loads .env for all test directories."""

from pathlib import Path

import pytest

from app.utils.config import load_env

_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def _load_env() -> None:
    if _ENV_PATH.exists():
        load_env(_ENV_PATH, override=True)


_load_env()


@pytest.fixture(autouse=True)
def _disable_system_keyring(monkeypatch) -> None:
    """Keep tests isolated from any real developer keychain entries."""
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


def pytest_configure(config):  # noqa: ARG001
    """Pytest hook — keep env available for collection and execution."""
    _load_env()
