"""Environment selection from the ``ENV`` variable."""

import pytest

from config.environment import Environment, get_environment


def test_environment_is_str_enum_with_stable_values() -> None:
    assert set(Environment) == {Environment.DEVELOPMENT, Environment.PRODUCTION}
    assert Environment.DEVELOPMENT.value == "development"
    assert Environment.PRODUCTION.value == "production"
    # StrEnum round-trips from its string value and compares equal to it,
    # which is what the `.value` call sites and `== Environment.X` checks rely on.
    assert Environment("production") is Environment.PRODUCTION
    assert Environment.PRODUCTION == "production"


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("production", Environment.PRODUCTION),
        ("prod", Environment.PRODUCTION),
        ("development", Environment.DEVELOPMENT),
        ("", Environment.DEVELOPMENT),
        ("anything-else", Environment.DEVELOPMENT),
    ],
)
def test_get_environment_maps_env_var(monkeypatch, env_value: str, expected: Environment) -> None:
    monkeypatch.setenv("ENV", env_value)

    assert get_environment() == expected
