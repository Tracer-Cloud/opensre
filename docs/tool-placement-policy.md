# Tool placement policy (T-20)

Where a piece of agent-callable capability lives is decided by **how many
vendor integrations its domain logic touches**, not by convenience or file
size. This is the decision rule referenced from
[ARCHITECTURE.md](ARCHITECTURE.md#tier-3--tools-and-integrations).

## Decision rule

Ask: does this tool's *purpose* depend on a specific external vendor or
SaaS backend (an `integrations/<vendor>/` package)?

1. **Single vendor** → `integrations/<vendor>/tools/`.
   The tool only makes sense in terms of one vendor's domain (a Datadog
   query, a GitHub mutation, a Sentry issue lookup). This is the default,
   most common case — see "Adding a Tool" in `AGENTS.md`.
2. **No vendor at all** → `tools/system/`.
   The tool's domain purpose has nothing to do with an external vendor —
   local process introspection, sandboxed code execution, RAG-style
   guidance retrieval. An *incidental* import of a vendor client for a
   side concern (e.g. resolving one of several possible credential
   sources, or picking a notification channel for delivery) does not
   make a tool vendor-specific; the test is the tool's reason to exist,
   not every import in the file.
3. **Genuinely spans 2+ vendor integrations** → `tools/cross_vendor/`.
   The tool's logic itself correlates or orchestrates across multiple
   `integrations/<vendor>/` packages — e.g. `fix_sentry_issue` reads from
   `integrations.sentry` and hands the fix to `integrations.pi`. This is
   the narrow bucket; don't reach for it just because a tool happens to
   format its output for a second vendor (e.g. "Slack-ready" report text
   is still single-vendor logic, not cross-vendor).
4. **True surface-level (CLI + REPL) duplication, not tool logic at all**
   → `surfaces/shared/` (see T-21). This is a different axis entirely —
   it's about presentation code two *surfaces* both need, not about where
   an agent-callable tool's business logic lives.

`tools/` also holds framework subsystems that aren't individual tools —
`tools/interactive_shell` (REPL action tools), `tools/registry.py` (the tool
registry itself). These
stay at the top level of `tools/`; the `system` / `cross_vendor` split only
applies to individual tool packages.

## Current state (as of T-19)

Applied to the pre-existing top-level `tools/` packages:

| Package | Placement | Why |
| --- | --- | --- |
| `tools/system/fleet_monitoring/` | system | Local AI-agent fleet monitoring; no vendor. |
| `tools/system/python_execution_tool/` | system | Generic sandboxed Python execution; the GitHub token import is one of several optional credential sources, not the tool's purpose. |
| `tools/system/sre_guidance_tool/` | system | Local knowledge-base retrieval; no vendor. |
| `tools/cross_vendor/fix_sentry_issue/` | cross_vendor | Reads a Sentry issue and hands the fix to the Pi coding agent — two `integrations/` packages in one tool's logic. |
| `tools/cross_vendor/resolve_merge_conflicts/` | cross_vendor | Reads the conflicts of a local git merge and hands them to the coding agent, then commits the merge — `integrations/git` and `integrations/coding_agent` in one tool's logic. |

**Migrated to their vendor packages** — every single-vendor tool now lives
under `integrations/<vendor>/tools/`, so rule 1 has no exceptions left:

- `integrations/github/tools/` — `architecture_issue_tool`,
  `community_followup_tool`, `git_deploy_timeline_tool`, `github_cli`,
  `work_status_report_tool`.
- `integrations/pi/tools/pi_coding_tool/` — Pi-only.
- `integrations/slack/tools/slack_send_message_tool/` — Slack-only.

A vendor's tool package is walked once its dotted path is listed in
`INTEGRATION_TOOL_PACKAGES` (`tools/registry_discovery.py`); nested tool
packages under it are discovered without further wiring.

## Registry mechanics

`tools/system/` and `tools/cross_vendor/` are ordinary packages discovered
by `tools/registry.py`'s top-level walk of `tools/`. Each declares a
`TOOL_MODULES` tuple in its `__init__.py` (the same manifest mechanism
`integrations/slack/tools/slack_send_message_tool/__init__.py` already uses for its own
`tool` submodule) listing the tool packages nested one level inside it.
Adding a new system or cross-vendor tool means adding its package name to
the relevant `TOOL_MODULES` tuple — no registry code changes required.
