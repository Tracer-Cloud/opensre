"""Tests for REPL config three-tier resolution."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from config.repl_config import ReplConfig


class TestReplConfigDefaults:
    def test_default_enabled_is_true(self) -> None:
        cfg = ReplConfig.load()
        assert cfg.enabled is True

    def test_default_layout_is_classic(self) -> None:
        cfg = ReplConfig.load()
        assert cfg.layout == "classic"

    def test_default_theme_is_purple(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENSRE_THEME", raising=False)
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        cfg = ReplConfig.load()
        assert cfg.theme == "purple"


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

    def test_opensre_theme_env_sets_theme(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "blue")
        assert ReplConfig.load().theme == "blue"

    def test_invalid_theme_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "nope")
        assert ReplConfig.load().theme == "purple"

    def test_invalid_theme_logs_warning(self, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "chartreuse")

        with caplog.at_level("WARNING"):
            cfg = ReplConfig.load()

        assert cfg.theme == "purple"
        assert "OPENSRE_THEME='chartreuse' is not a valid theme" in caplog.text


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

    def test_cli_theme_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_THEME", "green")
        cfg = ReplConfig.load(cli_theme="amber")
        assert cfg.theme == "amber"


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

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

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

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.layout == "pinned"

    def test_file_theme_is_read(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  theme: mono
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_THEME", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.theme == "mono"

    def test_invalid_file_theme_logs_warning(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            textwrap.dedent("""\
                interactive:
                  theme: chartreuse
            """),
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_THEME", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

        with caplog.at_level("WARNING"):
            cfg = ReplConfig.load()

        assert cfg.theme == "purple"
        assert "interactive.theme='chartreuse' is not a valid theme" in caplog.text

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

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

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

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load(cli_enabled=True, cli_layout="classic")
        assert cfg.enabled is True
        assert cfg.layout == "classic"

    def test_missing_file_falls_back_to_defaults(
        self, tmp_path: pytest.FixtureDef, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENSRE_INTERACTIVE", raising=False)
        monkeypatch.delenv("OPENSRE_LAYOUT", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

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

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

        cfg = ReplConfig.load()
        assert cfg.enabled is True
        assert cfg.layout == "classic"


class TestFromEnvAlias:
    def test_from_env_is_same_as_load_with_no_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")
        assert ReplConfig.from_env() == ReplConfig.load()


class TestThemeRegistry:
    def test_theme_registry_contains_expected_builtin_names(self) -> None:
        from infrastructure.terminal.theme import list_theme_names

        assert list_theme_names() == (
            "green",
            "blue",
            "amber",
            "mono",
            "red",
            "pink",
            "purple",
            "orange",
            "teal",
            "lime",
            "nord",
            "dracula",
            "solarized",
            "gruvbox",
            "webflux",
            "sunset",
        )

    def test_theme_registry_entries_include_required_semantic_tokens(self) -> None:
        from infrastructure.terminal.theme import get_theme, list_theme_names

        required = (
            "HIGHLIGHT",
            "BRAND",
            "TEXT",
            "SECONDARY",
            "DIM",
            "WARNING",
            "ERROR",
            "BG",
            "INPUT_SURFACE",
        )
        for name in list_theme_names():
            theme = get_theme(name)
            for token in required:
                value = getattr(theme, token)
                assert isinstance(value, str)
                assert value.startswith("#")
                assert len(value) == 7

    def test_lazy_rich_tokens_track_active_theme(self) -> None:
        from infrastructure.terminal.theme import BOLD_BRAND, HIGHLIGHT, set_active_theme

        set_active_theme("green")
        green_highlight = str(HIGHLIGHT)
        green_brand = str(BOLD_BRAND)
        set_active_theme("purple")
        assert str(HIGHLIGHT) != green_highlight
        assert str(BOLD_BRAND) != green_brand
        assert str(HIGHLIGHT).startswith("#")

    def test_set_active_theme_falls_back_to_default_for_unknown_name(self) -> None:
        from infrastructure.terminal.theme import (
            DEFAULT_THEME_NAME,
            get_active_theme,
            set_active_theme,
        )

        active = set_active_theme("does-not-exist")
        assert active.name == DEFAULT_THEME_NAME
        assert get_active_theme().name == DEFAULT_THEME_NAME

    def test_load_is_pure_and_never_activates_a_palette(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange: a palette is active and a different theme is configured.
        from infrastructure.terminal.theme import get_active_theme_name, set_active_theme

        monkeypatch.setenv("OPENSRE_THEME", "amber")
        set_active_theme("pink")

        # Act: resolving config must not touch the live terminal palette.
        cfg = ReplConfig.load()

        # Assert: the name is resolved, but activation stays the caller's job.
        assert cfg.theme == "amber"
        assert get_active_theme_name() == "pink"


class TestAlertListenerPortParsing:
    @pytest.mark.parametrize(
        ("raw_yaml_value", "expected_port", "should_warn"),
        [
            ("null", 0, True),
            ("true", 0, True),
            ("false", 0, True),
            ("3.14", 0, True),
            ("not_a_number", 0, True),
            ("-1", 0, True),
            ("65536", 0, True),
            ("0", 0, False),
            ("8080", 8080, False),
        ],
    )
    def test_file_backed_alert_listener_port(
        self,
        raw_yaml_value: str,
        expected_port: int,
        should_warn: bool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            f"interactive:\n  alert_listener_port: {raw_yaml_value}\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("OPENSRE_ALERT_LISTENER_PORT", raising=False)

        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)

        with caplog.at_level("WARNING"):
            cfg = ReplConfig.load()

        assert cfg.alert_listener_port == expected_port
        if should_warn:
            assert "interactive.alert_listener_port=" in caplog.text
        else:
            assert "interactive.alert_listener_port=" not in caplog.text

    @pytest.mark.parametrize(
        ("env_value", "expected_port", "should_warn"),
        [
            ("invalid", 0, True),
            ("-1", 0, True),
            ("65536", 0, True),
            ("0", 0, False),
            ("8080", 8080, False),
        ],
    )
    def test_env_backed_alert_listener_port(
        self,
        env_value: str,
        expected_port: int,
        should_warn: bool,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("OPENSRE_ALERT_LISTENER_PORT", env_value)

        with caplog.at_level("WARNING"):
            cfg = ReplConfig.load()

        assert cfg.alert_listener_port == expected_port
        if should_warn:
            assert "OPENSRE_ALERT_LISTENER_PORT=" in caplog.text
        else:
            assert "OPENSRE_ALERT_LISTENER_PORT=" not in caplog.text
