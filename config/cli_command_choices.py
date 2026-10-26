"""Lightweight discovery metadata for delegated CLI command groups."""

from __future__ import annotations

CLI_COMMAND_CHOICES: dict[tuple[str, ...], tuple[tuple[str, str], ...]] = {
    ("/auth",): (
        ("status", "Show local provider authentication"),
        ("login", "Sign in to a provider"),
        ("verify", "Verify provider authentication"),
        ("logout", "Sign out of a provider"),
    ),
    ("/config",): (("show", "Show local config"), ("set", "Set a config value")),
    ("/cron",): (
        ("list", "List scheduled deliveries"),
        ("add", "Add a scheduled delivery"),
        ("logs", "Show task execution history"),
        ("remove", "Remove a scheduled task"),
        ("run", "Run a task now"),
        ("status", "Show scheduler status"),
        ("start", "Start the scheduler daemon"),
    ),
    ("/debug",): (("sentry", "Send a Sentry diagnostic event"),),
    ("/guardrails",): (
        ("rules", "Show guardrail rules"),
        ("audit", "Show recent guardrail audit entries"),
        ("init", "Initialize guardrail rules"),
        ("test", "Test text against guardrails"),
    ),
    ("/messaging",): (
        ("status", "Show messaging security settings"),
        ("pair", "Create a pairing code"),
        ("allow", "Allow a messaging identity"),
        ("revoke", "Revoke a messaging identity"),
    ),
    ("/posthog",): (("report", "Run or schedule metric reports"),),
    ("/posthog", "report"): (
        ("run", "Run a report now"),
        ("schedule", "Manage scheduled reports"),
    ),
    ("/posthog", "report", "schedule"): (
        ("list", "List scheduled reports"),
        ("add", "Add a scheduled report"),
        ("run", "Run a scheduled report now"),
        ("remove", "Remove a scheduled report"),
    ),
    ("/runbooks",): (
        ("list", "List trusted runbook sources"),
        ("add", "Add a runbook source"),
        ("verify", "Verify a runbook source"),
        ("remove", "Remove a runbook source"),
    ),
    ("/sentry",): (
        ("digest", "Run or schedule morning digests"),
        ("uptime", "Check or watch uptime monitors"),
    ),
    ("/sentry", "digest"): (
        ("run", "Run a digest now"),
        ("schedule", "Manage scheduled digests"),
    ),
    ("/sentry", "digest", "schedule"): (
        ("list", "List scheduled digests"),
        ("add", "Add a scheduled digest"),
        ("run", "Run a scheduled digest now"),
        ("remove", "Remove a scheduled digest"),
    ),
    ("/sentry", "uptime"): (
        ("check", "Check current monitor health"),
        ("watch", "Manage uptime watches"),
    ),
    ("/sentry", "uptime", "watch"): (
        ("list", "List uptime watches"),
        ("add", "Add an uptime watch"),
        ("run", "Run an uptime watch now"),
        ("remove", "Remove an uptime watch"),
    ),
    ("/skills",): (
        ("status", "Show the active skills release"),
        ("update", "Fetch the latest skills release"),
        ("push", "Publish a skill"),
        ("rollback", "Roll back the skills release"),
        ("history", "Show skills release history"),
        ("pull", "Download a skill"),
    ),
}

__all__ = ["CLI_COMMAND_CHOICES"]
