"""Regression tests for the frozen-binary tiktoken plugin discovery bootstrap.

``litellm`` imports ``tiktoken`` at module load time and calls
``tiktoken.get_encoding("cl100k_base")`` immediately. tiktoken discovers that
encoding by walking the ``tiktoken_ext`` namespace package with
``pkgutil.iter_modules``, which only sees loose files on disk and finds
nothing in a PyInstaller frozen build -- crashing with ``ValueError: Unknown
encoding cl100k_base`` (``Plugins found: []``, see issue #3631). These tests
guard the bootstrap that bypasses the broken directory walk in frozen builds.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import tiktoken.registry as registry

from core.llm.transports.litellm.frozen_tiktoken_bootstrap import (
    ensure_tiktoken_encodings_discoverable,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC_FILE = _REPO_ROOT / "opensre.spec"


@pytest.fixture(autouse=True)
def _reset_tiktoken_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test from tiktoken's process-wide plugin/encoding caches."""
    monkeypatch.setattr(registry, "ENCODING_CONSTRUCTORS", None)
    monkeypatch.setattr(registry, "ENCODINGS", {})


def _simulate_broken_frozen_plugin_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduce the frozen-build failure: the namespace-package walk finds nothing."""
    monkeypatch.setattr(registry, "_available_plugin_modules", lambda: ())


def test_non_frozen_build_leaves_discovery_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside a frozen build the bootstrap must be a no-op."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    original = registry._available_plugin_modules

    ensure_tiktoken_encodings_discoverable()

    assert registry._available_plugin_modules is original


def test_frozen_build_without_bootstrap_reproduces_reported_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirms the failure mode this bootstrap exists to fix (issue #3631)."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    _simulate_broken_frozen_plugin_scan(monkeypatch)

    with pytest.raises(ValueError, match="Unknown encoding cl100k_base"):
        registry.get_encoding("cl100k_base")


def test_frozen_build_bootstrap_resolves_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the bootstrap applied, the frozen build must resolve the encoding."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    _simulate_broken_frozen_plugin_scan(monkeypatch)

    ensure_tiktoken_encodings_discoverable()
    encoding = registry.get_encoding("cl100k_base")

    assert encoding.name == "cl100k_base"


def test_release_spec_bundles_tiktoken_plugin() -> None:
    """The release build must hidden-import tiktoken's plugin module.

    Ties the bootstrap's direct-import target to the PyInstaller specification
    so renaming or dropping the hidden-import fails fast instead of only
    surfacing as a release-time binary crash.
    """
    spec = _SPEC_FILE.read_text(encoding="utf-8")

    assert '"tiktoken_ext.openai_public"' in spec
    assert '"tiktoken_ext"' in spec
