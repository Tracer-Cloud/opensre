"""Tests for REPL config three-tier resolution."""

from __future__ import annotations

import textwrap

import pytest

from app.cli.repl.config import ReplConfig


class TestReplConfigDefaults:
    def test_default_enabled_is_true(self) -> None:
        cfg = ReplConfig.load()
        assert cfg.enabled is True

    def test_default_layout_is_classic(self) -> None:
        cfg = ReplConfig.load()
        assert cfg.layout == "classic"


class TestEnvVarResolution:
    def test_opensre_interactive_0_disables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        assert ReplConfig.load().enabled is False

    def test_opensre_interactive_false_disables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "false")
        assert ReplConfig.load().enabled is False

    def test_opensre_interactive_off_disables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "off")
        assert ReplConfig.load().enabled is False

    def test_opensre_interactive_1_enables_repl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
        assert ReplConfig.load().enabled is True

    def test_opensre_layout_pinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        assert ReplConfig.load().layout == "pinned"

    def test_opensre_layout_classic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "classic")
        assert ReplConfig.load().layout == "classic"

    def test_invalid_layout_falls_back_to_classic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "fullscreen")
        assert ReplConfig.load().layout == "classic"


class TestCliOverride:
    def test_cli_enabled_false_wins_over_env_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
        cfg = ReplConfig.load(cli_enabled=False)
        assert cfg.enabled is False

    def test_cli_enabled_true_wins_over_env_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        cfg = ReplConfig.load(cli_enabled=True)
        assert cfg.enabled is True

    def test_cli_layout_pinned_wins_over_env_classic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "classic")
        cfg = ReplConfig.load(cli_layout="pinned")
        assert cfg.layout == "pinned"

    def test_cli_layout_classic_wins_over_env_pinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        cfg = ReplConfig.load(cli_layout="classic")
        assert cfg.layout == "classic"

    def test_cli_none_does_not_override_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        cfg = ReplConfig.load(cli_enabled=None)
        assert cfg.enabled is False


class TestFileResolution:
    def test_file_enabled_false_is_read(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: false
                  layout: classic
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is False

    def test_file_layout_pinned_is_read(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: true
                  layout: pinned
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.layout == "pinned"

    def test_env_overrides_file(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: false
                  layout: pinned
            """),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
        monkeypatch.setenv("OPENSRE_LAYOUT", "classic")

        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_cli_overrides_file_and_env(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  enabled: false
                  layout: pinned
            """),
            encoding="utf-8",
        )
        monkeypatch.setenv("OPENSRE_INTERACTIVE", "0")
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")

        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load(cli_enabled=True, cli_layout="classic")
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_missing_file_falls_back_to_defaults(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_malformed_file_falls_back_to_defaults(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(":::not valid yaml:::", encoding="utf-8")
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import app.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"


class TestFromEnvAlias:
    def test_from_env_is_same_as_load_with_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        assert ReplConfig.from_env() == ReplConfig.load()
