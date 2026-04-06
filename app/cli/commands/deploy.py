"""Deployment-related CLI commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import click

from app.cli.context import is_json_output, is_yes
from app.cli.errors import OpenSREError


def _deploy_style(questionary: Any) -> Any:
    return questionary.Style(
        [
            ("qmark", "fg:cyan bold"),
            ("question", "bold"),
            ("answer", "fg:cyan bold"),
            ("pointer", "fg:cyan bold"),
            ("highlighted", "fg:cyan bold"),
        ]
    )


def _get_deployment_status() -> dict[str, str]:
    """Load the current EC2 deployment state, if any."""
    try:
        from tests.shared.infrastructure_sdk.config import load_outputs

        outputs = load_outputs("tracer-ec2-remote")
        return {
            "ip": outputs.get("PublicIpAddress", ""),
            "instance_id": outputs.get("InstanceId", ""),
            "port": outputs.get("ServerPort", "8080"),
        }
    except (FileNotFoundError, Exception):  # noqa: BLE001
        return {}


def _persist_remote_url(outputs: Mapping[str, object]) -> None:
    ip = str(outputs.get("PublicIpAddress", ""))
    if not ip:
        return

    from app.cli.wizard.store import save_named_remote

    port = str(outputs.get("ServerPort", "8080"))
    url = f"http://{ip}:{port}"
    save_named_remote("ec2", url, set_active=True, source="deploy")
    click.echo(f"\n  Remote URL saved as 'ec2': {url}")
    click.echo("  You can now run:\n    opensre remote health")


def _run_deploy_interactive(ctx: click.Context) -> None:
    import questionary
    from rich.console import Console

    console = Console(highlight=False)
    style = _deploy_style(questionary)

    status = _get_deployment_status()
    if status.get("ip"):
        status_line = f"EC2 running at [bold]{status['ip']}:{status['port']}[/bold]"
    else:
        status_line = "[dim]no active deployment[/dim]"

    console.print()
    console.print(f"  [bold cyan]Deploy[/bold cyan]  {status_line}")
    console.print()

    choices: list[Any] = []

    if status.get("ip"):
        choices.extend([
            questionary.Choice("Check deployment health", value="health"),
            questionary.Choice("Tear down EC2 deployment", value="down"),
            questionary.Choice("Redeploy (tear down + deploy)", value="redeploy"),
        ])
    else:
        choices.append(
            questionary.Choice("Deploy to AWS EC2 (Bedrock)", value="ec2"),
        )

    choices.extend([
        questionary.Separator(),
        questionary.Choice("Exit", value="exit"),
    ])

    action = questionary.select(
        "What would you like to do?",
        choices=choices,
        style=style,
    ).ask()

    if action is None or action == "exit":
        return

    if action == "health":
        _check_deploy_health(status, console)
        return

    if action == "ec2":
        branch = questionary.text(
            "Git branch to deploy:",
            default="main",
            style=style,
        ).ask()
        if branch is None:
            return

        if not questionary.confirm(
            f"Deploy OpenSRE from branch '{branch}' to a new EC2 instance?",
            default=True,
            style=style,
        ).ask():
            console.print("  [dim]Cancelled.[/dim]")
            return

        ctx.invoke(deploy_ec2, down=False, branch=branch)
        return

    if action == "down":
        if not questionary.confirm(
            f"Tear down EC2 instance {status.get('instance_id', '')}?",
            default=False,
            style=style,
        ).ask():
            console.print("  [dim]Cancelled.[/dim]")
            return
        ctx.invoke(deploy_ec2, down=True, branch="main")
        return

    if action == "redeploy":
        branch = questionary.text(
            "Git branch to deploy:",
            default="main",
            style=style,
        ).ask()
        if branch is None:
            return

        if not questionary.confirm(
            f"Tear down current instance and redeploy from '{branch}'?",
            default=False,
            style=style,
        ).ask():
            console.print("  [dim]Cancelled.[/dim]")
            return

        console.print()
        console.print("  [bold]Tearing down existing deployment...[/bold]")
        ctx.invoke(deploy_ec2, down=True, branch="main")
        console.print()
        console.print("  [bold]Deploying fresh instance...[/bold]")
        ctx.invoke(deploy_ec2, down=False, branch=branch)


def _check_deploy_health(status: dict[str, str], console: Any) -> None:
    import httpx

    ip = status.get("ip", "")
    port = status.get("port", "8080")
    url = f"http://{ip}:{port}/ok"

    console.print(f"\n  Checking [bold]{url}[/bold] ...")
    try:
        resp = httpx.get(url, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        console.print(f"  [green]Healthy[/green]  {data}")
    except httpx.TimeoutException:
        console.print(f"  [red]Timeout[/red]  could not reach {ip}:{port}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]Unhealthy[/red]  {exc}")


@click.group(name="deploy", invoke_without_command=True)
@click.pass_context
def deploy(ctx: click.Context) -> None:
    """Deploy OpenSRE to a cloud environment."""
    if ctx.invoked_subcommand is None:
        if is_yes() or is_json_output():
            raise OpenSREError(
                "No subcommand provided.",
                suggestion="Use 'opensre deploy ec2' or 'opensre deploy ec2 --down'.",
            )
        _run_deploy_interactive(ctx)


@deploy.command(name="ec2")
@click.option(
    "--down",
    is_flag=True,
    default=False,
    help="Tear down the deployment instead of creating it.",
)
@click.option("--branch", default="main", help="Git branch to clone on the instance.")
def deploy_ec2(down: bool, branch: str) -> None:
    """Deploy the investigation server on an AWS EC2 instance.

    \b
    Uses Amazon Bedrock for LLM inference (no API key needed).
    The instance gets an IAM role with Bedrock access.

    \b
    Examples:
      opensre deploy ec2                 # spin up the server
      opensre deploy ec2 --down          # tear it down
      opensre deploy ec2 --branch main   # deploy from a specific branch
    """
    if down:
        from tests.deployment.ec2.infrastructure_sdk.destroy_remote import destroy

        destroy()
        return

    from tests.deployment.ec2.infrastructure_sdk.deploy_remote import deploy as run_deploy

    _persist_remote_url(run_deploy(branch=branch))
