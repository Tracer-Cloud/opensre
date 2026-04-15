"""Railway deployment support for OpenSRE.

This module provides functionality to deploy OpenSRE to Railway using the Railway CLI.
It handles:
- CLI detection and auth/token setup
- App deployment using the root Dockerfile
- Environment variable configuration
- Health check polling before reporting success
"""

from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from collections.abc import Sequence

READINESS_PATH = "/ok"


class DeployResult(TypedDict, total=False):
    """Result type for deploy_to_railway function."""

    success: bool
    url: str | None
    logs: list[str]
    dry_run: bool
    health_ok: bool
    error: str | None


def _run_command(
    cmd: Sequence[str],
    *,
    capture: bool = True,
    check: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell command with consistent error handling."""
    kwargs: dict[str, Any] = {"check": check, "text": True}
    if capture:
        kwargs["capture_output"] = True
    if timeout is not None:
        kwargs["timeout"] = timeout

    try:
        result: subprocess.CompletedProcess[str] = subprocess.run(cmd, **kwargs)  # noqa: S603
        return result
    except FileNotFoundError:
        # Command not found - CLI not installed
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=127,
            stdout="",
            stderr=f"Command not found: {cmd[0]}",
        )


def is_railway_cli_installed() -> bool:
    """Check if the Railway CLI is installed and available."""
    result = _run_command(["railway", "--version"], capture=True, check=False)
    return result.returncode == 0


def get_railway_auth_status() -> dict[str, str | bool]:
    """Check if the user is authenticated with Railway.

    Returns a dict with 'authenticated' (bool) and 'detail' (str) keys.
    """
    result = _run_command(["railway", "whoami"], capture=True, check=False)
    if result.returncode == 0:
        return {"authenticated": True, "detail": result.stdout.strip()}
    return {
        "authenticated": False,
        "detail": result.stderr.strip() or "Not authenticated with Railway",
    }


def _extract_railway_url(stdout: str) -> str | None:
    """Extract the deployment URL from Railway CLI output."""
    # Railway CLI typically outputs URLs in the format:
    # https://<app-name>.up.railway.app or similar
    import re

    # Look for URLs in the output
    url_pattern = r"https://[^\s\)]+\.up\.railway\.app"
    matches: list[str] = re.findall(url_pattern, stdout)
    if matches:
        return matches[-1]  # Return the last match (most recent)

    # Alternative pattern for Railway URLs
    alt_pattern = r"https://[^\s\)]+\.railway\.app"
    matches = re.findall(alt_pattern, stdout)
    if matches:
        return matches[-1]

    return None


def deploy_to_railway(
    *,
    project_name: str | None = None,
    service_name: str | None = None,
    env_vars: dict[str, str] | None = None,
    wait_for_health: bool = True,
    health_timeout: float = 300.0,
    health_interval: float = 5.0,
    dry_run: bool = False,
    auth_detail: str | None = None,
) -> DeployResult:
    """Deploy the application to Railway.

    Args:
        project_name: Optional Railway project name to link to
        service_name: Optional Railway service name
        env_vars: Optional dict of environment variables to set
        wait_for_health: Whether to poll the readiness endpoint after deploy
        health_timeout: Maximum time to wait for health check (seconds)
        health_interval: Interval between health checks (seconds)
        dry_run: If True, only print what would be done without executing
        auth_detail: Optional pre-validated Railway auth detail

    Returns:
        Dict with deployment results including 'success', 'url', and 'logs' keys
    """
    import httpx

    if dry_run:
        return {
            "success": True,
            "url": "https://example.up.railway.app",
            "logs": ["[dry-run] Would deploy to Railway"],
            "dry_run": True,
        }

    logs: list[str] = []

    if auth_detail is None:
        # Step 1: Verify CLI is installed
        if not is_railway_cli_installed():
            return {
                "success": False,
                "url": None,
                "logs": logs + ["Error: Railway CLI is not installed"],
                "error": "Railway CLI not found. Install with: npm install -g @railway/cli",
            }

        logs.append("Railway CLI detected")

        # Step 2: Verify authentication
        auth_status = get_railway_auth_status()
        if not auth_status["authenticated"]:
            return {
                "success": False,
                "url": None,
                "logs": logs + [f"Error: {auth_status['detail']}"],
                "error": "Not authenticated with Railway. Run 'railway login' first.",
            }

        auth_detail = str(auth_status["detail"])
    else:
        logs.append("Using pre-validated Railway auth")

    logs.append(f"Authenticated as: {auth_detail}")

    # Step 3: Link to project if specified
    if project_name:
        logs.append(f"Linking to project: {project_name}")
        link_result = _run_command(
            ["railway", "link", "--project", project_name],
            capture=True,
            check=False,
        )
        if link_result.returncode != 0:
            logs.append(f"Warning: Could not link to project: {link_result.stderr}")

    # Step 4: Set environment variables if provided
    if env_vars:
        for key, value in env_vars.items():
            logs.append(f"Setting environment variable: {key}")
            set_result = _run_command(
                ["railway", "variables", "set", f"{key}={value}"],
                capture=True,
                check=False,
            )
            if set_result.returncode != 0:
                logs.append(f"Warning: Failed to set {key}: {set_result.stderr}")

    # Step 5: Deploy
    logs.append("Starting deployment...")
    deploy_cmd = ["railway", "up", "--detach"]
    if service_name:
        deploy_cmd.extend(["--service", service_name])

    deploy_result = _run_command(deploy_cmd, capture=True, check=False)

    if deploy_result.returncode != 0:
        return {
            "success": False,
            "url": None,
            "logs": logs + [f"Deployment failed: {deploy_result.stderr}"],
            "error": f"Deployment failed: {deploy_result.stderr}",
        }

    logs.append("Deployment initiated")

    # Step 6: Get the deployment URL
    # Try to extract from deploy output, or use railway domain command
    url = _extract_railway_url(deploy_result.stdout + deploy_result.stderr)

    if not url:
        # Try to get domain via CLI
        domain_result = _run_command(
            ["railway", "domain"],
            capture=True,
            check=False,
        )
        if domain_result.returncode == 0:
            raw_domain = domain_result.stdout.strip()
            if raw_domain:
                url = raw_domain if "://" in raw_domain else f"https://{raw_domain}"

    if url:
        logs.append(f"Deployment URL: {url}")
    else:
        logs.append("Warning: Could not determine deployment URL")

    # Step 7: Wait for health check if requested and URL is available
    health_ok = False
    if wait_for_health and url:
        logs.append(f"Waiting for health check at {url}{READINESS_PATH}...")
        health_start = time.time()
        while time.time() - health_start < health_timeout:
            try:
                response = httpx.get(
                    f"{url}{READINESS_PATH}",
                    timeout=10.0,
                    follow_redirects=True,
                )
                if response.status_code == 200:
                    logs.append("Health check passed!")
                    health_ok = True
                    break
            except Exception as exc:  # noqa: BLE001
                logs.append(f"Health check pending... ({type(exc).__name__})")

            time.sleep(health_interval)
        else:
            logs.append(f"Health check timed out after {health_timeout}s")
    elif wait_for_health and not url:
        logs.append("Skipping health check: no URL available")

    return {
        "success": True,
        "url": url,
        "logs": logs,
        "health_ok": health_ok,
        "error": None,
    }


def run_deploy(
    *,
    target: str = "railway",
    project_name: str | None = None,
    service_name: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
) -> int:
    """Run the deployment command.

    Args:
        target: Deployment target (currently only 'railway' is supported)
        project_name: Optional project name for Railway
        service_name: Optional service name for Railway
        dry_run: If True, simulate the deployment without executing
        yes: If True, skip confirmation prompts

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    from rich.console import Console

    console = Console(highlight=False)

    if target != "railway":
        console.print(f"[red]Error: Unsupported deployment target '{target}'")
        console.print("[dim]Currently only 'railway' is supported.")
        return 1

    # Check prerequisites
    if not is_railway_cli_installed():
        console.print("[red]Error: Railway CLI is not installed[/red]")
        console.print("[dim]Install with: npm install -g @railway/cli[/dim]")
        return 1

    auth = get_railway_auth_status()
    if not auth["authenticated"]:
        console.print("[red]Error: Not authenticated with Railway[/red]")
        console.print("[dim]Run: railway login[/dim]")
        return 1

    # Confirm deployment unless --yes
    if not dry_run and not yes:
        try:
            import questionary

            confirmed = questionary.confirm(
                "Deploy OpenSRE to Railway?",
                default=True,
            ).ask()
            if not confirmed:
                console.print("[dim]Deployment cancelled.")
                return 0
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Aborted.")
            return 1

    # Run deployment
    if dry_run:
        console.print("[dim]Dry-run mode: simulating deployment...")

    console.print(f"[cyan]Deploying to {target}...")

    result = deploy_to_railway(
        project_name=project_name,
        service_name=service_name,
        wait_for_health=True,
        dry_run=dry_run,
        auth_detail=str(auth["detail"]),
    )

    # Print logs
    for log_line in result["logs"]:
        console.print(f"  {log_line}")

    if result["success"]:
        if result.get("url"):
            console.print("\n[green]Deployment successful![/green]")
            console.print(f"[dim]URL: {result['url']}[/dim]")
            if result.get("health_ok"):
                console.print("[green]Health check: PASSED[/green]")
            else:
                console.print("[yellow]Health check: PENDING (may still be starting)[/yellow]")
        else:
            console.print("\n[green]Deployment initiated![/green]")
        return 0

    console.print(f"\n[red]Deployment failed: {result.get('error', 'Unknown error')}[/red]")
    return 1
