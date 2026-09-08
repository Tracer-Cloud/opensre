---
name: slack-handoff
description: >-
  Connect OpenSRE to Slack and show how to hand off DevOps chores from a
  channel mention or a DM. Verifies with cli_exec; if Slack is missing, queues
  `/integrations setup slack` via slash_invoke (the wizard needs a full
  terminal). Use for the startup demo option "Connect OpenSRE to Slack and
  hand off DevOps chores for your team". Never post, reply, or send to Slack
  in this flow. Multi-step; load before acting.
getting_started: Connect OpenSRE to Slack and hand off DevOps chores for your team
demo_order: 3
metadata:
  owner: Tracer Team
  usecases:
    - First-experience demo: connect OpenSRE to Slack and show the handoff path
    - Verify Slack is configured, then run the Slack setup wizard if it is missing
    - Explain how a team hands off DevOps chores from a Slack mention or DM
  requires:
    - Slack workspace the user can add the OpenSRE bot to
    - Interactive terminal for `/integrations setup slack` when Slack is not configured
  type: onboarding
  version: "1.0"
tools:
  - cli_exec
  - slash_invoke
---
══════════════════════════════════════════════════════════
SLACK HANDOFF SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE:
- The user picked "Connect OpenSRE to Slack and hand off DevOps chores for your team"
  from the startup demo menu (option C), or asks to set up Slack and show how to
  hand off DevOps chores from Slack.

USE THESE TOOLS:
- `cli_exec`
- `slash_invoke`

DO NOT USE THIS SKILL FOR:
- Posting, replying, or reacting in Slack. This demo never sends to Slack.
- CI/CD analytics or the reliability agent. Use `cicd-analytics-demo` or
  `cicd-reliability-agent`.

HARD RULES:
- Never call Slack send/reply/react tools.
- Never invent that Slack is connected; trust only `cli_exec` verify results.
- Never call `cli_exec` with `integrations setup slack`. That wizard is
  interactive and `cli_exec` will refuse it.
- After setup (or a successful verify), explain in two sentences how to hand
  off a chore: mention OpenSRE in a channel it can see, or DM it.

Steps, in order:

1) Check Slack.
   Call `cli_exec` with payload `integrations verify slack`.

2) Set up if needed.
   If Slack is not configured, call `slash_invoke` with
   `/integrations setup slack` and stop. The shell queues that wizard on the
   next prompt so it gets exclusive stdin. If Slack is already connected, say
   so and skip setup.

3) Explain the hand-off.
   Two sentences: mention OpenSRE in a channel or DM it; do not post anything
   from this demo. Then stop.

Step labeling rules (UX):
- Before every numbered step's tool calls, emit this exact header format as
  assistant text in the SAME response as the tool calls, then one short
  status sentence:
    ### [n/3] <step name>
    <One-sentence status.>
- Never start tool calls for a new step without its header.
