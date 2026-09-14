"""Tests for interactive-shell CLI reference grounding cache."""

from __future__ import annotations

import click
import pytest

import surfaces.interactive_shell.grounding.cli_reference as cli_reference_module
from surfaces.interactive_shell.session.session import Session


def _reference_with_cli() -> cli_reference_module.CliReference:
    """Return a :class:`CliReference` wired to the shell's CLI command group.

    CLI catalog assembly lives in ``surfaces/`` (T-05 — see issue #3538); tests
    inject the group the same way ``ShellPromptContextProvider`` does.
    """
    from surfaces.cli.app import cli

    ref = cli_reference_module.CliReference()
    ref.set_command_group_provider(lambda: cli)
    return ref


def test_second_build_is_cache_hit() -> None:
    ref = _reference_with_cli()
    ref.build_text()
    s1 = ref.stats()
    ref.build_text()
    s2 = ref.stats()
    assert s2.hits == s1.hits + 1
    assert s2.misses == s1.misses


def test_cold_build_is_silent(capsys: pytest.CaptureFixture[str]) -> None:
    from surfaces.cli.app import cli

    text = _reference_with_cli().build_text()
    captured = capsys.readouterr()
    first_command = sorted(name for name, command in cli.commands.items() if not command.hidden)[0]

    assert captured.out == ""
    assert captured.err == ""
    assert "=== opensre --help ===" in text
    assert f"=== opensre {first_command} --help ===" in text
    assert f"Usage: opensre {first_command}" in text


def test_invalidate_forces_rebuild_miss() -> None:
    ref = _reference_with_cli()
    ref.build_text()
    s1 = ref.stats()
    assert s1.misses == 1
    ref.invalidate()
    assert ref.stats().misses == 0
    ref.build_text()
    s2 = ref.stats()
    assert s2.misses == 1
    assert s2.cached is True


def test_signature_change_busts_cli_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    ref = _reference_with_cli()
    monkeypatch.setattr(
        cli_reference_module,
        "_current_cli_signature",
        lambda *_args, **_kwargs: "sig-a",
    )
    ref.build_text()
    monkeypatch.setattr(
        cli_reference_module,
        "_current_cli_signature",
        lambda *_args, **_kwargs: "sig-b",
    )
    ref.build_text()
    stats = ref.stats()
    assert stats.misses >= 2
    assert stats.signature == "sig-b"


def test_invalidate_resets_hit_miss_counters() -> None:
    ref = _reference_with_cli()
    ref.build_text()
    ref.build_text()
    assert ref.stats().hits >= 1
    ref.invalidate()
    s = ref.stats()
    assert s.hits == 0
    assert s.misses == 0


def test_non_cacheable_short_output_skips_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_reference_module,
        "_build_cli_reference_text_uncached",
        lambda *_args, **_kwargs: "too short",
    )
    ref = _reference_with_cli()
    ref.build_text()
    ref.build_text()
    stats = ref.stats()
    assert stats.cached is False
    assert stats.misses >= 2


def test_non_cacheable_long_without_sentinel_skips_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filler = "x" * 120
    monkeypatch.setattr(
        cli_reference_module,
        "_build_cli_reference_text_uncached",
        lambda *_args, **_kwargs: filler,
    )
    ref = _reference_with_cli()
    ref.build_text()
    assert ref.stats().cached is False


def test_reference_without_provider_returns_placeholder() -> None:
    """Without a command-group provider the cache emits a placeholder — no surface imports."""
    ref = cli_reference_module.CliReference()
    text = ref.build_text()
    assert "=== opensre --help ===" in text
    assert "not available in this runtime" in text
    # Placeholder is intentionally short-lived: it must not populate the cache.
    assert ref.stats().cached is False


def test_command_group_provider_is_bound_lazily() -> None:
    """The provider is invoked only when :meth:`build_text` runs, not at bind time."""
    calls: list[int] = []

    def _provider() -> click.Command:
        calls.append(1)
        group = click.Group("opensre")
        group.add_command(click.Command("noop"))
        return group

    ref = cli_reference_module.CliReference()
    ref.set_command_group_provider(_provider)
    assert not calls
    ref.build_text()
    assert calls == [1]


def _session_with_cli() -> Session:
    from surfaces.cli.app import cli

    session = Session()
    session.terminal.cli_command_group = cli
    return session


def test_shell_prompt_context_provider_includes_cli_reference() -> None:
    provider = cli_reference_module.shell_prompt_context_provider(_session_with_cli())
    text = provider.cli_reference()
    assert "=== opensre --help ===" in text
    assert "Usage: opensre" in text
    assert "Every nonempty shell command runs through the platform command shell" in text
    assert "plain commands are parsed to argv" not in text


def test_shell_reference_hints_omit_runtime_confirmation_policy() -> None:
    hints = cli_reference_module._interactive_shell_slash_hints()  # noqa: SLF001
    assert "platform command shell" in hints
    assert "/auto" not in hints
    assert "Plan-only" not in hints


def test_shell_prompt_context_provider_reuses_session_cli_cache() -> None:
    session = _session_with_cli()
    first = cli_reference_module.shell_prompt_context_provider(session)
    second = cli_reference_module.shell_prompt_context_provider(session)
    first.cli_reference()
    second.cli_reference()
    assert second._cli.stats().hits >= 1  # noqa: SLF001 - session-scoped cache reuse
