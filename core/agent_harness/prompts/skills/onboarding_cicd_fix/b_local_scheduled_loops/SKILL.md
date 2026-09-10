---
name: cicd-reliability-agent
description: >-
  Schedules a recurring CI/CD reliability agent for one repository: scan local
  checkouts, pick the repo, then schedule_ci_reliability_loop (weekday 08:00
  local by default, inbox only). Use for the startup demo option "Set up an
  agent that improves CI/CD reliability over time". Not a one-shot analysis
  (cicd-analytics-demo) and not a current-failing-checks read (github-ci-health).
  Multi-step; load before acting.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Yauhen
  last_changed_at: 2026-09-09
  usecases:
    - First-experience demo: schedule a weekday CI/CD reliability agent for one repo
    - Watch CI reliability over time without a one-shot analytics report
    - Recurring weekday CI reliability check delivered to the shell inbox
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (optional; a named repository also works)
  type: report
  version: "1.1"
tools:
  - scan_local_git_workspace
  - schedule_ci_reliability_loop
  - ask_user_choice
references:
  - common/ask_once.md
after_tool:
  - after: scan_local_git_workspace
    tool: ask_user_choice
    args:
      title: Which repository should the agent watch?
    options_from: local_git_scan_repos
    options_extra:
      - Use the open-source example repository (Tracer-Cloud/opensre)
---

# CI/CD reliability agent

Schedule a recurring CI/CD reliability check for one repository, delivered to
the shell inbox.

## Workflow rules

- Never run `gh`, `git`, or `shell_run` for this flow.
- If a tool reports a missing GitHub token, say `opensre integrations setup github`
  and stop. Do not fall back to another data source.
- Decision points use `ask_user_choice` with the exact option texts below.
  End the turn after calling it; the answer arrives as the next user message.
- Output `schedule_ci_reliability_loop`'s `response_text` exactly and stop.

## Plan

Track progress with the `update_plan` tool, not with headers or prose:

- On entry, before the first workflow tool call, call `update_plan` with the
  steps below verbatim, the first step `in_progress`, and a one-line
  `explanation` (this is not a diagnosis; no hypothesis table):
  `Scan this machine` / `Pick the repository` / `Schedule the loop`.
- When the request already names the repository, omit the first two steps
  from the plan instead of renumbering.
- After a step's tool results, call `update_plan` marking it `completed` and
  the next step `in_progress`, in the same response as the next step's tool
  calls. When the next step is an `ask_user_choice`, mark the step and call
  the menu in the same response, then end the turn.
- Do not narrate the plan or repeat step names in prose; the shell renders
  the checklist.

## Workflow

### 1. Scan this machine

Call `scan_local_git_workspace()` with no arguments. Say in one sentence
what was found, using `summary` from the result.

### 2. Pick the repository

The menu opens by itself after the scan: it is declared in this skill's
frontmatter and the host runs it. Do not call `ask_user_choice` for this
question, and do not write your own version of it. End the turn and wait; the
answer arrives as the next user message.

### 3. Schedule the loop

Call `schedule_ci_reliability_loop(owner="<owner>", repo="<repo>",
include_report=true)`. The loop runs weekdays at 08:00 local time; the card
states the schedule and how to run it at another time, so do not ask about the
cadence.
Output `response_text` verbatim and stop. When a same-day report exists it
comes first, then the schedule card; otherwise the card alone.
