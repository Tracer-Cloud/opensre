"""Root pytest configuration — loads .env for all test directories."""

import os
from pathlib import Path

import pytest

from app.config import LLMSettings, resolve_llm_provider
from app.services import reset_llm_singletons
from app.utils.config import load_env

_PROJECT_ROOT = Path(__file__).parent.parent
_ENV_PATH = _PROJECT_ROOT / ".env"


def _load_env() -> None:
    if _ENV_PATH.exists():
        load_env(_ENV_PATH, override=True)


os.environ["OPENSRE_DISABLE_KEYRING"] = "1"
_load_env()


@pytest.fixture(autouse=True)
def _disable_system_keyring(monkeypatch) -> None:
    """Keep tests isolated from any real developer keychain entries."""
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")


@pytest.fixture(autouse=True)
def _ensure_valid_llm_provider(monkeypatch) -> None:
    """Use a configured provider key if the current provider is incomplete."""
    selected_provider = resolve_llm_provider()
    current_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if selected_provider != current_provider:
        monkeypatch.setenv("LLM_PROVIDER", selected_provider)


@pytest.fixture(autouse=True)
def _reset_llm_singletons() -> None:
    """Reset any cached LLM client singletons before each test."""
    reset_llm_singletons()


def _llm_settings_available() -> bool:
    try:
        LLMSettings.from_env()
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """Skip real-LLM tests at collection time when no valid provider is configured."""
    if _llm_settings_available():
        return

    skip_marker = pytest.mark.skip(
        reason="Skipping real LLM test: no valid LLM provider credentials are configured."
    )
    for item in items:
        if item.get_closest_marker("requires_llm"):
            item.add_marker(skip_marker)


def pytest_runtest_setup(item):
    if item.get_closest_marker("requires_llm") and not _llm_settings_available():
        pytest.skip(
            "Skipping real LLM test: no valid LLM provider credentials are configured."
        )


def pytest_configure(config):  # noqa: ARG001
    """Pytest hook — keep env available for collection and execution."""
    _load_env()
