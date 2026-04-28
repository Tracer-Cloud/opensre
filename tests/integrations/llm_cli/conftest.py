from __future__ import annotations

import pytest

from app.integrations.llm_cli.binary_resolver import npm_prefix_bin_dirs


@pytest.fixture(autouse=True)
def clear_npm_prefix_bin_dirs_cache() -> None:
    """Prevent env/platform cache leakage across llm_cli tests."""
    npm_prefix_bin_dirs.cache_clear()
    yield
    npm_prefix_bin_dirs.cache_clear()
