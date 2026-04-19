"""Deployment-related CLI commands."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import click

from app.analytics.cli import (
    capture_cli_invoked,
    capture_deploy_completed,
    capture_deploy_failed,
    capture_deploy_started,
)
from app.cli.context import is_json_output, is_yes
from app.cli.errors import OpenSREError
from app.cli.langsmith_deploy import (
    extract_deployment_url,
    is_langgraph_cli_installed,
    persist_langsmith_env,
    resolve_deployment_name,
    resolve_langsmith_api_key,
    run_langsmith_deploy,
    validate_langsmith_api_key,
)
from app.deployment.ec2_config import load_remote_outputs
from app.deployment.health import poll_deployment_health


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
        outputs = load_remote_outputs()
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


def _prompt_deploy_branch(questionary: Any, style: Any, *, default: str = "main") -> str | None:
    """Prompt for the git branch to deploy."""
    branch = questionary.text(
        "Git branch to deploy:",
        default=default,
        style=style,
    ).ask()
    if branch is None:
        return None
    resolved = str(branch).strip()
    return resolved or default


def _redeploy_ec2(ctx: click.Context, *, branch: str, console: Any) -> None:
    """Tear down the managed EC2 instance and deploy a fresh one."""
    console.print()
    console.print("  [bold]Tearing down existing deployment...[/bold]")
    ctx.invoke(deploy_ec2, down=True, branch="main")  # branch unused when down=True
    console.print()
    console.print("  [bold]Deploying fresh instance...[/bold]")
    ctx.invoke(deploy_ec2, down=False, branch=branch)


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
        choices.extend(
            [
                questionary.Choice("Check deployment health", value="health"),
                questionary.Choice("Tear down EC2 deployment", value="down"),
                questionary.Choice("Redeploy (tear down + deploy)", value="redeploy"),
            ]
        )
    else:
        choices.append(
            questionary.Choice("Deploy to AWS EC2 (Bedrock)", value="ec2"),
        )

    choices.append(
        questionary.Choice("Deploy to LangSmith (LangGraph)", value="langsmith"),
    )

    choices.extend(
        [
            questionary.Separator(),
            questionary.Choice("Exit", value="exit"),
        ]
    )

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
        branch = _prompt_deploy_branch(questionary, style)
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

    if action == "railway":
        ctx.invoke(deploy_railway)
        return

    if action == "langsmith":

        # 2. LangGraph CLI check
        ok, msg = is_langgraph_cli_installed()
        if not ok:
            console.print(f"[red]{msg}[/red]")
            return

        # 3. API key resolve
        api_key = resolve_langsmith_api_key()
        if not api_key:
            api_key = questionary.password("LangSmith API key:").ask()
            if not api_key:
                return

        # 4. Validate API key
        valid, msg = validate_langsmith_api_key(api_key)
        if not valid:
            console.print(f"[red]{msg}[/red]")
            return

        # 5. Deployment name
        deployment_name = resolve_deployment_name()

        if not questionary.confirm(
            f"Deploy to LangSmith with deployment '{deployment_name}'?",
            default=True,
            style=style,
        ).ask():
            console.print("  [dim]Cancelled.[/dim]")
            return

        # 6. Persist to .env
        persist_langsmith_env(api_key, deployment_name)

        capture_cli_invoked()
        capture_deploy_started(target="langsmith", dry_run=False)

        # 7. Deploy
        console.print("[cyan]Deploying to LangSmith...[/cyan]")
        code, output = run_langsmith_deploy(
            api_key=api_key,
            deployment_name=deployment_name,
            build_only=False,
        )

        console.print(output)

        # 8. Show URL
        if code == 0:
            capture_deploy_completed(target="langsmith", dry_run=False)
            url = extract_deployment_url(output)
            if url:
                console.print(f"[green]Deployment URL:[/green] {url}")
            else:
                console.print("[yellow]Deployment succeeded but no URL found[/yellow]")
        else:
            capture_deploy_failed(target="langsmith", dry_run=False)
            console.print("[red]Deployment failed[/red]")

        return

    if action == "down":
        if not questionary.confirm(
            f"Tear down EC2 instance {status.get('instance_id', '')}?",
            default=False,
            style=style,
        ).ask():
            console.print("  [dim]Cancelled.[/dim]")
            return
        ctx.invoke(deploy_ec2, down=True, branch="main")  # branch unused when down=True
        return

    if action == "redeploy":
        branch = _prompt_deploy_branch(questionary, style)
        if branch is None:
            return

        if not questionary.confirm(
            f"Tear down current instance and redeploy from '{branch}'?",
            default=False,
            style=style,
        ).ask():
            console.print("  [dim]Cancelled.[/dim]")
            return

        _redeploy_ec2(ctx, branch=branch, console=console)


def _check_deploy_health(status: dict[str, str], console: Any) -> None:
    ip = status.get("ip", "")
    port = status.get("port", "8080")
    base_url = f"http://{ip}:{port}"

    console.print(f"\n  Checking [bold]{base_url}[/bold] ...")
    try:
        health = poll_deployment_health(
            base_url,
            interval_seconds=2.0,
            max_attempts=3,
            request_timeout_seconds=5.0,
        )
        console.print(
            f"  [green]Healthy[/green]  endpoint={health.url} "
            f"attempts={health.attempts} elapsed={health.elapsed_seconds:.1f}s"
        )
    except TimeoutError:
        console.print(f"  [red]Timeout[/red]  could not reach {ip}:{port}")
    except Exception as exc:  # noqa: BLE001
        console.print(f"  [red]Unhealthy[/red]  {exc}")


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
        if is_yes() or is_json_output():
            raise OpenSREError(
                "No subcommand provided.",
                suggestion="Use 'opensre deploy ec2', 'opensre deploy ec2 --down', or 'opensre deploy langsmith'.",
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

    from app.cli.commands.remote_health import run_remote_health_check
    from tests.deployment.ec2.infrastructure_sdk.deploy_remote import deploy as run_deploy

    outputs = run_deploy(branch=branch)
    _persist_remote_url(outputs)

    remote_url = _build_remote_url(outputs)
    if remote_url:
        click.echo("\n  Running remote deployment health check...")
        try:
            run_remote_health_check(base_url=remote_url, output_json=False, save_url=False)
        except click.ClickException as exc:
            click.echo(f"\n  [warn] Health check: {exc.format_message()}", err=True)
            click.echo("  Deployment provisioned. Retry with: opensre remote health")


@deploy.command(name="railway")
@click.option("--project", "project_name", default=None, help="Railway project name.")
@click.option("--service", "service_name", default=None, help="Railway service name.")
@click.option("--dry-run", is_flag=True, default=False, help="Simulate deployment only.")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt.")
def deploy_railway(
    project_name: str | None,
    service_name: str | None,
    dry_run: bool,
    yes: bool,
) -> None:
    """Deploy OpenSRE to Railway."""
    from app.cli.deploy import run_deploy

    capture_cli_invoked()
    capture_deploy_started(target="railway", dry_run=dry_run)
    exit_code = run_deploy(
        target="railway",
        project_name=project_name,
        service_name=service_name,
        dry_run=dry_run,
        yes=yes,
    )
    if exit_code == 0:
        capture_deploy_completed(target="railway", dry_run=dry_run)
        return

    capture_deploy_failed(target="railway", dry_run=dry_run)
    raise SystemExit(exit_code)


@deploy.command(name="langsmith")
@click.option("--api-key", default=None, help="LangSmith API key")
@click.option("--build-only", is_flag=True, help="Build without deploy")
@click.option("--deployment-name", default=None, help="Deployment name")
def deploy_langsmith(api_key: str | None, build_only: bool, deployment_name: str | None) -> None:
    """Deploy OpenSRE to LangSmith."""

    ok, msg = is_langgraph_cli_installed()
    if not ok:
        raise click.ClickException(msg)

    api_key = resolve_langsmith_api_key(api_key)
    if not api_key:
        raise click.ClickException("LangSmith API key not found.")

    valid, msg = validate_langsmith_api_key(api_key)
    if not valid:
        raise click.ClickException(msg)

    deployment_name = resolve_deployment_name(deployment_name)

    persist_langsmith_env(api_key, deployment_name)

    click.echo("Deploying to LangSmith...")
    code, output = run_langsmith_deploy(
        api_key=api_key,
        deployment_name=deployment_name,
        build_only=build_only,
    )

    click.echo(output)

    if code != 0:
        raise SystemExit(code)

    url = extract_deployment_url(output)
    if url:
        click.echo(f"Deployment URL: {url}")

