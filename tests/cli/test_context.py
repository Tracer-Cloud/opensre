from __future__ import annotations

import click

from infrastructure.process.runtime_flags import (
    configure_runtime_flags,
    is_debug,
    is_json_output,
    is_onboarding_enabled,
    is_verbose,
    is_yes,
    reset_runtime_flags,
)
from surfaces.cli.runtime_flags import sync_runtime_flags_from_click


def test_is_json_output_true() -> None:
    reset_runtime_flags()
    configure_runtime_flags(json=True)
    assert is_json_output() is True


def test_is_json_output_false() -> None:
    reset_runtime_flags()
    configure_runtime_flags(json=False)
    assert is_json_output() is False


def test_is_verbose_true() -> None:
    reset_runtime_flags()
    configure_runtime_flags(verbose=True)
    assert is_verbose() is True


def test_is_verbose_false() -> None:
    reset_runtime_flags()
    configure_runtime_flags(verbose=False)
    assert is_verbose() is False


def test_is_debug_true() -> None:
    reset_runtime_flags()
    configure_runtime_flags(debug=True)
    assert is_debug() is True


def test_is_debug_false() -> None:
    reset_runtime_flags()
    configure_runtime_flags(debug=False)
    assert is_debug() is False


def test_is_yes_true() -> None:
    reset_runtime_flags()
    configure_runtime_flags(yes=True)
    assert is_yes() is True


def test_is_yes_false() -> None:
    reset_runtime_flags()
    configure_runtime_flags(yes=False)
    assert is_yes() is False


def test_onboarding_defaults_to_enabled_and_can_be_disabled() -> None:
    reset_runtime_flags()
    assert is_onboarding_enabled() is True

    configure_runtime_flags(onboarding=False)
    assert is_onboarding_enabled() is False


def test_defaults_without_configuration() -> None:
    reset_runtime_flags()
    assert is_json_output() is False
    assert is_verbose() is False
    assert is_debug() is False
    assert is_yes() is False
    assert is_onboarding_enabled() is True


def test_sync_runtime_flags_from_click_root() -> None:
    reset_runtime_flags()
    root_ctx = click.Context(click.Command("root"))
    root_ctx.obj = {
        "json": True,
        "verbose": False,
        "debug": True,
        "yes": True,
        "skip_onboarding": True,
    }

    child_ctx = click.Context(click.Command("child"), parent=root_ctx)
    child_ctx.obj = {"json": False}

    sync_runtime_flags_from_click(child_ctx)
    assert is_json_output() is True
    assert is_verbose() is False
    assert is_debug() is True
    assert is_yes() is True
    assert is_onboarding_enabled() is False


def test_sync_runtime_flags_from_click_none_obj() -> None:
    reset_runtime_flags()
    ctx = click.Context(click.Command("root"))
    ctx.obj = None
    sync_runtime_flags_from_click(ctx)
    assert is_json_output() is False
    assert is_verbose() is False
    assert is_debug() is False
    assert is_yes() is False
    assert is_onboarding_enabled() is True
