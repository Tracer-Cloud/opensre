from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.cli.commands.remote import (
    _build_deploy_choices,
    _build_investigation_choices,
    _managed_ec2_deployment_status,
    _render_preflight_status,
)
from app.remote.client import PreflightResult
from app.remote.stream import StreamEvent


class _AnsweredPrompt:
    def __init__(self, answer: str | bool | None) -> None:
        self._answer = answer

    def ask(self) -> str | bool | None:
        return self._answer


def test_remote_health_requires_saved_or_explicit_url() -> None:
    runner = CliRunner()

    with patch("app.cli.wizard.store.load_remote_url", return_value=None):
        result = runner.invoke(cli, ["remote", "health"])

    assert result.exit_code != 0
    assert "No remote URL configured." in result.output


def test_remote_health_uses_saved_url_and_persists_normalized_url() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.preflight.return_value = PreflightResult(
        ok=True,
        version="2026.4.5",
        server_type="lightweight",
        endpoints=["/investigate"],
        latency_ms=12,
    )

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
        patch("app.remote.client.RemoteAgentClient", return_value=client) as mock_client_cls,
        patch("app.cli.wizard.store.save_remote_url") as mock_save_remote_url,
    ):
        result = runner.invoke(cli, ["remote", "health"])

    assert result.exit_code == 0
    mock_client_cls.assert_called_once_with("10.0.0.1", api_key=None)
    mock_save_remote_url.assert_called_once_with("http://10.0.0.1:2024")


def test_remote_health_uses_preflight_capabilities_for_output() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.preflight.return_value = PreflightResult(
        ok=True,
        version="2026.4.5",
        server_type="lightweight",
        endpoints=["/investigate"],
        latency_ms=12,
    )

    with (
        patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
        patch("app.remote.client.RemoteAgentClient", return_value=client),
        patch("app.cli.wizard.store.save_remote_url"),
    ):
        result = runner.invoke(cli, ["remote", "health"])

    assert result.exit_code == 0
    assert "lightweight" in result.output
    assert "/investigate" in result.output
    assert "Live events" in result.output
    assert "unavailable" in result.output


def test_remote_trigger_persists_url_after_successful_run() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.trigger_investigation.return_value = iter([StreamEvent("end", data={})])
    renderer = MagicMock()

    with (
        patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
        patch("app.remote.client.RemoteAgentClient", return_value=client),
        patch("app.remote.renderer.StreamRenderer", return_value=renderer),
        patch("app.cli.wizard.store.save_remote_url") as mock_save_remote_url,
    ):
        result = runner.invoke(cli, ["remote", "trigger"])

    assert result.exit_code == 0
    mock_save_remote_url.assert_called_once_with("http://10.0.0.1:2024")
    renderer.render_stream.assert_called_once()


def test_remote_health_reports_timeout_cleanly() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.preflight.side_effect = httpx.TimeoutException("timed out")

    with patch("app.remote.client.RemoteAgentClient", return_value=client):
        result = runner.invoke(cli, ["remote", "--url", "10.0.0.1", "health"])

    assert result.exit_code == 1
    assert "Connection timed out reaching http://10.0.0.1:2024." in result.output


def test_remote_group_passes_api_key_to_client() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.preflight.return_value = PreflightResult(
        ok=True,
        server_type="lightweight",
        endpoints=["/investigate", "/investigate/stream"],
        latency_ms=10,
    )

    with (
        patch("app.remote.client.RemoteAgentClient", return_value=client) as mock_client_cls,
        patch("app.cli.wizard.store.save_remote_url"),
    ):
        result = runner.invoke(
            cli,
            ["remote", "--url", "10.0.0.1", "--api-key", "secret", "health"],
        )

    assert result.exit_code == 0
    mock_client_cls.assert_called_once_with("10.0.0.1", api_key="secret")


# ---------------------------------------------------------------------------
# Preflight status rendering
# ---------------------------------------------------------------------------


class TestRenderPreflightStatus:
    def test_no_url_configured(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rich.console import Console

        console = Console(highlight=False, force_terminal=True)
        _render_preflight_status("", "custom", None, console)
        captured = capsys.readouterr()
        assert "no remote URL configured" in captured.out

    def test_healthy_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rich.console import Console

        console = Console(highlight=False, force_terminal=True)
        pf = PreflightResult(
            ok=True,
            version="0.5.2",
            server_type="lightweight",
            endpoints=["/investigate", "/investigate/stream"],
            latency_ms=142,
        )
        _render_preflight_status("http://host:2024", "ec2", pf, console)
        captured = capsys.readouterr()
        assert "v0.5.2" in captured.out
        assert "142ms" in captured.out
        assert "stream" in captured.out

    def test_unreachable_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        from rich.console import Console

        console = Console(highlight=False, force_terminal=True)
        pf = PreflightResult(ok=False, error="connection refused")
        _render_preflight_status("http://host:2024", "ec2", pf, console)
        captured = capsys.readouterr()
        assert "connection refused" in captured.out


# ---------------------------------------------------------------------------
# Adaptive menu
# ---------------------------------------------------------------------------


class TestBuildInvestigationChoices:
    def test_full_capabilities_shows_stream_options(self) -> None:
        import questionary

        pf = PreflightResult(
            ok=True,
            server_type="lightweight",
            endpoints=["/investigate", "/investigate/stream"],
        )
        choices = _build_investigation_choices(pf, questionary)
        values = [c.value for c in choices]
        assert "investigate" in values
        assert "investigate-sample" in values

    def test_langgraph_server_shows_langgraph_options(self) -> None:
        import questionary

        pf = PreflightResult(
            ok=True,
            server_type="langgraph",
            endpoints=["/threads", "/threads/*/runs/stream"],
        )
        choices = _build_investigation_choices(pf, questionary)
        values = [c.value for c in choices]
        assert "investigate-langgraph" in values
        assert "investigate-sample-langgraph" in values

    def test_old_server_disables_streamless_options(self) -> None:
        import questionary

        pf = PreflightResult(
            ok=True,
            server_type="lightweight",
            endpoints=["/investigate"],
        )
        choices = _build_investigation_choices(pf, questionary)
        assert len(choices) == 2
        assert choices[0].disabled is not None
        assert choices[1].disabled is not None

    def test_unreachable_server_disables_options(self) -> None:
        import questionary

        pf = PreflightResult(ok=False, error="connection refused")
        choices = _build_investigation_choices(pf, questionary)
        assert len(choices) == 1
        assert choices[0].disabled is not None


class TestManagedDeployChoices:
    def test_managed_ec2_status_matches_saved_ec2_remote(self) -> None:
        with patch(
            "app.cli.commands.deploy._get_deployment_status",
            return_value={
                "ip": "44.210.116.190",
                "instance_id": "i-123",
                "port": "8080",
            },
        ):
            status = _managed_ec2_deployment_status(
                "http://44.210.116.190:8080",
                "ec2",
            )

        assert status["instance_id"] == "i-123"
        assert status["url"] == "http://44.210.116.190:8080"

    def test_build_deploy_choices_highlights_streaming_redeploy(self) -> None:
        import questionary

        choices = _build_deploy_choices(
            {"ip": "44.210.116.190", "port": "8080", "url": "http://44.210.116.190:8080"},
            PreflightResult(
                ok=True,
                server_type="lightweight",
                endpoints=["/investigate"],
            ),
            questionary,
        )

        assert len(choices) == 2
        assert choices[1].value == "redeploy-ec2"
        assert "enable streaming" in str(choices[1].title)


# ---------------------------------------------------------------------------
# Stream 404 fallback
# ---------------------------------------------------------------------------


class TestStreamInvestigationFallback:
    def test_investigate_command_404_requires_streaming_upgrade(self) -> None:
        """When /investigate/stream returns 404 on a lightweight server,
        the CLI should not downgrade to the blocking endpoint automatically."""
        runner = CliRunner()
        client = MagicMock()
        client.base_url = "http://10.0.0.1:8080"

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client.stream_investigate.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_resp,
        )
        client.preflight.return_value = PreflightResult(
            ok=True,
            version="0.4.0",
            server_type="lightweight",
            endpoints=["/investigate"],
            latency_ms=100,
        )

        with (
            patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1:8080"),
            patch("app.remote.client.RemoteAgentClient", return_value=client),
            patch("app.cli.wizard.store.save_remote_url"),
        ):
            result = runner.invoke(cli, ["remote", "investigate", "--sample"])

        assert result.exit_code == 1
        assert "Live investigation streaming is unavailable on this server" in result.output
        assert "legacy blocking request" in result.output
        client.investigate.assert_not_called()

    def test_investigate_command_404_falls_back_to_langgraph(self) -> None:
        """When /investigate/stream returns 404 on a LangGraph server,
        the CLI should auto-switch to the trigger path."""
        runner = CliRunner()
        client = MagicMock()
        client.base_url = "http://10.0.0.1:2024"

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        client.stream_investigate.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=mock_resp,
        )
        client.preflight.return_value = PreflightResult(
            ok=True,
            version="",
            server_type="langgraph",
            endpoints=["/threads", "/threads/*/runs/stream"],
            latency_ms=80,
        )
        renderer = MagicMock()
        client.trigger_investigation.return_value = iter([StreamEvent("end", data={})])

        with (
            patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
            patch("app.remote.client.RemoteAgentClient", return_value=client),
            patch("app.remote.renderer.StreamRenderer", return_value=renderer),
            patch("app.cli.wizard.store.save_remote_url"),
        ):
            result = runner.invoke(cli, ["remote", "investigate", "--sample"])

        assert result.exit_code == 0
        assert "LangGraph deployment detected" in result.output


def test_remote_interactive_redeploy_refreshes_to_new_remote_url() -> None:
    import questionary

    runner = CliRunner()
    select_answers = iter(["redeploy-ec2", "exit"])
    old_url = "http://10.0.0.1:8080"
    new_url = "http://10.0.0.2:8080"
    stream_unavailable = PreflightResult(
        ok=True,
        version="2026.4.5",
        server_type="lightweight",
        endpoints=["/investigate"],
        latency_ms=20,
    )
    streaming_ready = PreflightResult(
        ok=True,
        version="2026.4.6",
        server_type="lightweight",
        endpoints=["/investigate", "/investigate/stream"],
        latency_ms=18,
    )

    def _select(*args: object, **kwargs: object) -> _AnsweredPrompt:
        return _AnsweredPrompt(next(select_answers))

    with (
        patch("app.cli.wizard.store.load_remote_url", side_effect=[old_url, new_url]),
        patch(
            "app.cli.wizard.store.load_named_remotes",
            side_effect=[{"ec2": old_url}, {"ec2": new_url}],
        ),
        patch("app.cli.wizard.store.load_active_remote_name", side_effect=["ec2", "ec2"]),
        patch("app.cli.commands.remote._run_preflight", side_effect=[stream_unavailable, streaming_ready]) as mock_preflight,
        patch("app.cli.commands.deploy._redeploy_ec2") as mock_redeploy,
        patch.object(questionary, "select", side_effect=_select),
        patch.object(questionary, "text", return_value=_AnsweredPrompt("feature/streaming")),
        patch.object(questionary, "confirm", return_value=_AnsweredPrompt(True)),
    ):
        result = runner.invoke(cli, ["remote"])

    assert result.exit_code == 0
    mock_redeploy.assert_called_once()
    assert mock_preflight.call_count == 2
    assert mock_preflight.call_args_list[1].args[0] == new_url
