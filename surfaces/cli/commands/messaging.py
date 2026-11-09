"""CLI commands for messaging security: DM pairing and identity management."""

from __future__ import annotations

import time

import click
from rich.console import Console

from integrations.messaging_security import (
    MessagingIdentityPolicy,
    generate_pairing_code,
    hash_pairing_code,
)
from integrations.store import get_integration, upsert_instance
from surfaces.shared.messaging_options import messaging_parameters, validate_allow_user_id

_console = Console(highlight=False)


def _load_identity_policy(service: str) -> tuple[dict | None, MessagingIdentityPolicy]:
    """Load the integration record and its identity policy."""
    record = get_integration(service)
    if record is None:
        return None, MessagingIdentityPolicy()

    credentials = record.get("credentials", {})
    raw_policy = credentials.get("identity_policy")
    if raw_policy and isinstance(raw_policy, dict):
        policy = MessagingIdentityPolicy.model_validate(raw_policy)
    else:
        policy = MessagingIdentityPolicy()
    return record, policy


def _save_identity_policy(
    service: str, record: dict | None, policy: MessagingIdentityPolicy
) -> None:
    """Persist the identity policy back into the integration store.

    Uses upsert_instance for both new and existing records to ensure a
    consistent code path. When no record exists, upsert_instance creates
    one automatically. This avoids the problem where a later
    upsert_integration call (e.g. from the wizard) would replace the
    stub record and silently drop the identity_policy.
    """
    if record is None:
        # No existing record — upsert_instance will create one.
        upsert_instance(
            service,
            {
                "name": "default",
                "tags": {},
                "credentials": {"identity_policy": policy.model_dump(mode="json")},
            },
        )
    else:
        # Read the existing instance name and credentials, merge the policy,
        # and write back only that instance.
        instances = record.get("instances", [])
        first_instance = instances[0] if instances else {}
        instance_name = (
            first_instance.get("name", "default") if isinstance(first_instance, dict) else "default"
        )
        credentials = dict(record.get("credentials", {}))
        credentials["identity_policy"] = policy.model_dump(mode="json")
        upsert_instance(
            service,
            {
                "name": instance_name,
                "tags": first_instance.get("tags", {}) if isinstance(first_instance, dict) else {},
                "credentials": credentials,
            },
            record_id=record.get("id"),
        )


@click.group("messaging", invoke_without_command=True)
@click.pass_context
def messaging(ctx: click.Context) -> None:
    """Messaging security: DM pairing and identity management."""
    # No subcommand: show help and exit 0 (a bare group is a help request here,
    # not an error) instead of Click's default missing-command exit code 2.
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@messaging.command("pair", params=messaging_parameters("pair"))
def pair_command(platform: str) -> None:
    """Generate a one-time pairing code for DM authentication.

    The operator runs this command, receives a code, and sends it to the
    bot via DM as `/pair <code>`. On success the bot adds the sender to
    the allowed-users list.
    """
    service = platform.lower()
    record, policy = _load_identity_policy(service)

    was_disabled = not policy.inbound_enabled

    # Generate pairing code
    code = generate_pairing_code()
    policy.pairing_secret_hash = hash_pairing_code(code)
    policy.pairing_created_at = time.time()
    policy.pairing_attempts = 0
    policy.require_dm_pairing = True
    policy.inbound_enabled = True

    _save_identity_policy(service, record, policy)

    if was_disabled:
        _console.print(f"[yellow]Note: inbound messaging has been enabled for {platform}.[/yellow]")
    _console.print(f"\n[bold green]Pairing code generated for {platform}:[/bold green]")
    _console.print(f"\n  [bold yellow]{code}[/bold yellow]\n")
    _console.print(
        f"Next: open [bold]{platform}[/bold] (not this shell), DM your bot, and send: "
        f"[dim]/pair {code}[/dim]"
    )
    _console.print(
        f"[dim]The {platform} gateway must be running to receive it "
        f"(e.g. `opensre gateway start`).[/dim]"
    )
    _console.print("[dim]The code is single-use and will expire in 15 minutes.[/dim]\n")


@messaging.command("allow", params=messaging_parameters("allow"))
def allow_command(platform: str, user_id: str) -> None:
    """Manually add a user to the allowed-users list (bypasses DM pairing)."""
    service = platform.lower()
    user_id = validate_allow_user_id(service, user_id)
    record, policy = _load_identity_policy(service)

    if user_id in policy.allowed_user_ids:
        _console.print(
            f"[yellow]User {user_id} is already in the allowed list for {platform}.[/yellow]"
        )
        return

    was_disabled = not policy.inbound_enabled
    policy.allowed_user_ids.append(user_id)
    policy.inbound_enabled = True
    _save_identity_policy(service, record, policy)

    if was_disabled:
        _console.print(f"[yellow]Note: inbound messaging has been enabled for {platform}.[/yellow]")

    _console.print(f"[green]Added user {user_id} to {platform} allowed list.[/green]")


@messaging.command("revoke", params=messaging_parameters("revoke"))
def revoke_command(platform: str, user_id: str) -> None:
    """Remove a user from the allowed-users list."""
    service = platform.lower()
    record, policy = _load_identity_policy(service)

    if user_id not in policy.allowed_user_ids:
        _console.print(
            f"[yellow]User {user_id} is not in the allowed list for {platform}.[/yellow]"
        )
        return

    policy.allowed_user_ids.remove(user_id)
    # Clear any pending pairing code so the revoked user cannot re-pair via a
    # code that was generated after their revocation.
    policy.pairing_secret_hash = None
    policy.pairing_created_at = None
    policy.pairing_attempts = 0
    _save_identity_policy(service, record, policy)

    _console.print(f"[green]Removed user {user_id} from {platform} allowed list.[/green]")


@messaging.command("status", params=messaging_parameters("status"))
def status_command(platform: str) -> None:
    """Show the current messaging security status for a platform."""
    service = platform.lower()
    record, policy = _load_identity_policy(service)

    _console.print(f"\n[bold]Messaging Security Status — {platform}[/bold]\n")

    if record is None:
        _console.print(f"[yellow]No {platform} integration configured.[/yellow]")
        _console.print("[dim]Run the setup wizard or configure the integration first.[/dim]\n")
        return

    _console.print(f"  Inbound enabled:     {'Yes' if policy.inbound_enabled else 'No'}")
    _console.print(f"  DM pairing required: {'Yes' if policy.require_dm_pairing else 'No'}")
    _console.print(f"  Pairing pending:     {'Yes' if policy.pairing_secret_hash else 'No'}")
    _console.print(f"  Rejection behavior:  {policy.rejection_behavior.value}")
    _console.print(f"  Allowed users:       {len(policy.allowed_user_ids)}")
    if policy.allowed_user_ids:
        for uid in policy.allowed_user_ids:
            _console.print(f"    - {uid}")
    _console.print(f"  Allowed chats:       {len(policy.allowed_chat_ids)}")
    if policy.allowed_chat_ids:
        for cid in policy.allowed_chat_ids:
            _console.print(f"    - {cid}")
    _console.print()
