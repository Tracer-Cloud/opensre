from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from surfaces.cli.app import cli


def _patch_config_home(monkeypatch, tmp_path: Path) -> Path:
    opensre_home = tmp_path / ".opensre"
    monkeypatch.setattr("config.constants.OPENSRE_HOME_DIR", opensre_home)
    monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", opensre_home)
    return opensre_home


def test_config_show_inspects_local_file_not_env(monkeypatch, tmp_path: Path) -> None:
    opensre_home = _patch_config_home(monkeypatch, tmp_path)
    config_path = opensre_home / "config.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump({"interactive": {"enabled": False, "layout": "classic"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENSRE_INTERACTIVE", "1")
    monkeypatch.setenv("OPENSRE_LAYOUT", "pinned")

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert "on-disk values" in result.output
    assert "interactive:" in result.output
    assert "enabled: false" in result.output
    assert "layout: classic" in result.output
    assert "pinned" not in result.output


def test_config_set_round_trips_layout(monkeypatch, tmp_path: Path) -> None:
    opensre_home = _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    set_result = runner.invoke(cli, ["config", "set", "interactive.layout", "pinned"])
    assert set_result.exit_code == 0
    assert "interactive.layout = pinned" in set_result.output

    config_path = opensre_home / "config.yml"
    assert config_path.exists()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["interactive"]["layout"] == "pinned"

    show_result = runner.invoke(cli, ["config", "show"])
    assert show_result.exit_code == 0
    assert "layout: pinned" in show_result.output


def test_config_set_round_trips_enabled(monkeypatch, tmp_path: Path) -> None:
    opensre_home = _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    set_result = runner.invoke(cli, ["config", "set", "interactive.enabled", "false"])
    assert set_result.exit_code == 0
    assert "interactive.enabled = False" in set_result.output

    config_path = opensre_home / "config.yml"
    assert config_path.exists()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["interactive"]["enabled"] is False


def test_config_set_invalid_enabled_value_returns_helpful_error(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["config", "set", "interactive.enabled", "maybe"])

    assert result.exit_code != 0
    assert "Invalid value for interactive.enabled" in result.output


def test_config_set_invalid_layout_value_returns_helpful_error(monkeypatch, tmp_path: Path) -> None:
    _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["config", "set", "interactive.layout", "fullscreen"])

    assert result.exit_code != 0
    assert "Invalid value for interactive.layout" in result.output


def test_config_set_malformed_file_preserves_contents(monkeypatch, tmp_path: Path) -> None:
    opensre_home = _patch_config_home(monkeypatch, tmp_path)
    config_path = opensre_home / "config.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    # Unclosed flow sequence — PyYAML rejects this reliably (unlike "::: ..." edge cases).
    original_text = "key: [\n"
    config_path.write_text(original_text, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "interactive.layout", "pinned"])

    assert result.exit_code != 0
    assert "Could not parse local config file" in result.output
    assert config_path.read_text(encoding="utf-8") == original_text


def test_config_show_handles_empty_file(monkeypatch, tmp_path: Path) -> None:
    opensre_home = _patch_config_home(monkeypatch, tmp_path)
    config_path = opensre_home / "config.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert "on-disk values" in result.output
    assert "{}" in result.output


def test_config_set_unknown_key_returns_helpful_error(monkeypatch, tmp_path: Path) -> None:
    _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["config", "set", "foo.bar", "value"])

    assert result.exit_code != 0
    assert "Unknown config key 'foo.bar'" in result.output
    assert "interactive.enabled" in result.output
    assert "interactive.layout" in result.output
    assert "interactive.theme" in result.output


def test_config_set_round_trips_theme(monkeypatch, tmp_path: Path) -> None:
    opensre_home = _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    set_result = runner.invoke(cli, ["config", "set", "interactive.theme", "blue"])
    assert set_result.exit_code == 0
    assert "interactive.theme = blue" in set_result.output

    config_path = opensre_home / "config.yml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["interactive"]["theme"] == "blue"


def test_config_set_invalid_theme_value_returns_helpful_error(monkeypatch, tmp_path: Path) -> None:
    _patch_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["config", "set", "interactive.theme", "chartreuse"])

    assert result.exit_code != 0
    assert "Invalid value for interactive.theme" in result.output
