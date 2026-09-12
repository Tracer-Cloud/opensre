---
name: connecting-slack
description: >-
  Connect OpenSRE to Slack and show how to hand off DevOps chores from a channel mention or a DM. Verifies
  with cli_exec; if Slack is missing, queues `/integrations setup slack` via slash_invoke (the wizard
  needs a full terminal). Use for the startup demo option "Connect OpenSRE to Slack and hand off DevOps
  chores for your team". Never post, reply, or send to Slack in this flow. Multi-step; load before acting.
getting_started: Connect OpenSRE to Slack and hand off DevOps chores for your team
demo_order: 3
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-12
  usecases:
  - For Slack workspace administrators connecting OpenSRE for their team.
  - For users verifying an existing Slack connection and learning channel or DM handoffs.
  requires:
  - Permission to add the OpenSRE bot to the target Slack workspace.
  - The cli_exec and slash_invoke tools.
  - When Slack setup is needed, an interactive terminal for /integrations setup slack.
  version: '1.2'
---

# Slack handoff

Verify or set up Slack, then explain how the team can hand off DevOps chores
through a channel mention or a DM.

## Workflow rules

- Never call Slack send/reply/react tools.
- Never invent that Slack is connected; trust only `cli_exec` verify results.
- Never call `cli_exec` with `integrations setup slack`. That wizard is
  interactive and `cli_exec` will refuse it.
- After setup (or a successful verify), explain in two sentences how to hand
  off a chore: mention OpenSRE in a channel it can see, or DM it.

## Plan

Track progress with the `update_plan` tool, not with headers or prose:

- On entry, before the first workflow tool call, call `update_plan` with the
  steps below verbatim, the first step `in_progress`, and a one-line
  `explanation` (this is not a diagnosis; no hypothesis table):
  `Check Slack` / `Set up if needed` / `Explain the hand-off`.
- After a step's tool results, call `update_plan` marking it `completed` and
  the next step `in_progress`, in the same response as the next step's single
  tool call. When Slack is already connected, mark `Set up if needed` completed
  without new tool calls instead of dropping it mid-run.
- Do not narrate the plan or repeat step names in prose; the shell renders
  the checklist.

## Workflow

### 1. Check Slack

Call `cli_exec` with payload `integrations verify slack`.

### 2. Set up if needed

If Slack is not configured, call `slash_invoke` with
`/integrations setup slack` and stop. The shell queues that wizard on the
next prompt so it gets exclusive stdin. If Slack is already connected, say
so and skip setup.

### 3. Explain the hand-off

Two sentences: mention OpenSRE in a channel or DM it; do not post anything
from this demo. Then stop.
