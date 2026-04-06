"""Deployment-related CLI commands."""

from __future__ import annotations

from collections.abc import Mapping

import click


def _print_deploy_targets() -> None:
    click.echo("Available deployment targets:\n")
    click.echo("  opensre deploy ec2          Deploy investigation server on AWS EC2 (Bedrock)")
    click.echo("  opensre deploy ec2 --down   Tear down the EC2 deployment")
    click.echo("\nRun 'opensre deploy <target> --help' for details.")


def _persist_remote_url(outputs: Mapping[str, object]) -> None:
    ip = str(outputs.get("PublicIpAddress", ""))
    if not ip:
        return

    from app.cli.wizard.store import save_remote_url

    port = str(outputs.get("ServerPort", "8080"))
    save_remote_url(f"http://{ip}:{port}")
    click.echo("\n  Remote URL saved. You can now run:\n    opensre remote health")


def _build_remote_url(outputs: Mapping[str, object]) -> str | None:
    ip = str(outputs.get("PublicIpAddress", "")).strip()
    if not ip:
        return None
    port = str(outputs.get("ServerPort", "8080")).strip() or "8080"
    return f"http://{ip}:{port}"


@click.group(name="deploy", invoke_without_command=True)
@click.pass_context
def deploy(ctx: click.Context) -> None:
    """Deploy OpenSRE to a cloud environment."""
    if ctx.invoked_subcommand is None:
        _print_deploy_targets()


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

    from app.cli.commands.remote import run_remote_health_check
    from tests.deployment.ec2.infrastructure_sdk.deploy_remote import deploy as run_deploy

    outputs = run_deploy(branch=branch)
    _persist_remote_url(outputs)

    remote_url = _build_remote_url(outputs)
    if remote_url:
        click.echo("\n  Running remote deployment health check...")
        run_remote_health_check(base_url=remote_url, output_json=False, save_url=False)
