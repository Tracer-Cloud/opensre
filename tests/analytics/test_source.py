from __future__ import annotations

import pytest

from infrastructure.analytics.source import is_test_run, resolve_environment_tag


def test_is_test_run_true_for_explicit_boolean_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_IS_TEST", "1")

    assert is_test_run() is True


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("PYTEST_CURRENT_TEST", "tests/suite.py::test_case"),
        ("GITHUB_ACTIONS", "true"),
        ("CI", "true"),
    ],
)
def test_is_test_run_true_for_auto_detected_env(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv(env_name, env_value)

    assert is_test_run() is True


def test_is_test_run_false_without_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_CICD", raising=False)
    monkeypatch.delenv("OPENSRE_IS_TEST", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)

    assert is_test_run() is False


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("production", "prod"),
        ("prod", "prod"),
        ("staging", "staging"),
        ("stage", "staging"),
        ("development", "dev"),
        ("dev", "dev"),
        ("local", "dev"),
        ("preview", "unknown"),
    ],
)
def test_resolve_environment_tag_maps_known_values(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str,
    expected: str,
) -> None:
    monkeypatch.delenv("OPENSRE_ANALYTICS_ENV", raising=False)
    monkeypatch.setenv("ENV", env_value)

    assert resolve_environment_tag() == expected
