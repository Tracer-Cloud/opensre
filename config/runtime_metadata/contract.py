"""The runtime-fact contract shared by every consumer.

One source of truth for the fact key sets and the shell-command deny-list.
The prompt renderer, the sandbox tool schema, the contract tests, and the
smoke validators all derive from these — adding a fact is one probe, one
tuple entry, one prompt producer.
"""

from __future__ import annotations

# Reserved key merged into ``execute_python_code`` inputs (never overwrite user keys).
RUNTIME_INPUTS_KEY = "opensre_runtime"

# Session-static facts: cached on ``SessionCore.runtime_metadata`` at bootstrap
# because nothing here changes turn to turn.
STATIC_FACT_KEYS: tuple[str, ...] = (
    "opensre_version",
    "opensre_build",
    "runtime_env",
    "os_family",
    "tz_name",
    "python_version",
    "pid",
    "ppid",
    "tools",
    "kubeconfig",
    "hostname",
    "scratchpad_dir",
    "cloud_provider",
    "cloud_region",
    "workspace_repo",
    "network_egress",
    "shell_available",
    "capability_warnings",
)

# Captured fresh per prompt render / sandbox call; never cached at bootstrap.
# Disk/memory keys are absent when psutil fails (degraded, not fatal).
LIVE_FACT_KEYS: tuple[str, ...] = (
    "now_iso",
    "uptime_seconds",
    "disk_used_percent",
    "disk_free_gb",
    "memory_used_percent",
    "memory_available_gb",
)

# Shell commands the agent must never reach for — every fact (or the sandbox
# socket/pathlib guidance) replaces one of these. Rendered into both the
# assistant prompt guidance and the python-execution tool description. Do not
# add cloud-instance-metadata link-local addresses here or into prompts:
# naming them teaches the model the reflex we are removing.
BLOCKED_INTROSPECTION_COMMANDS: tuple[str, ...] = (
    "opensre --version",
    "python --version",
    "kubectl version",
    "which",
    "ps",
    "date",
    "uptime",
    "hostname",
    "uname",
    "ls",
    "df",
    "free",
    "top",
    "curl",
    "ping",
    "nslookup",
)


__all__ = [
    "BLOCKED_INTROSPECTION_COMMANDS",
    "LIVE_FACT_KEYS",
    "RUNTIME_INPUTS_KEY",
    "STATIC_FACT_KEYS",
]
