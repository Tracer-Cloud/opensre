---
name: scheduling-github-ci-fixes
description: >-
  Schedules a recurring CI/CD reliability report for one repository: scan local
  checkouts, pick the repo, analyze GitHub Actions now, then
  schedule_ci_reliability_loop (weekday 08:00 local by default, inbox only).
  Use for the startup demo option "Set up an agent that improves CI/CD reliability over time".
  Not a one-shot analysis without a schedule (analyzing-github-ci-performance) and not a
  current-failing-checks read (reporting-github-ci-failures). Multi-step; load before
  acting.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-11
  usecases:
    - First-experience demo: analyze once, then schedule a weekday CI reliability report
    - Watch CI reliability over time after a live first report
    - Recurring weekday CI reliability check delivered to the shell inbox
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (optional; a named repository also works)
  type: report
  version: "1.2"
tools:
  - scan_local_git_workspace
  - analyze_github_ci_reliability
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

Analyze one repository's CI/CD reliability now, then schedule the same
report for weekday mornings in this shell's inbox.

## Workflow rules

- Never run `gh`, `git`, or `shell_run` for this flow.
- If a tool reports a missing GitHub token, say `opensre integrations setup github`
  and stop. Do not fall back to another data source.
- If the analysis result is not successful for any other reason (the
  repository cannot be read, a rate limit, an error), say why in one line and
  stop. Do not schedule a loop whose first report never appeared.
- Decision points use `ask_user_choice` with the exact option texts below.
  End the turn after calling it; the answer arrives as the next user message.
- The analyze tool paints the report; do not restate its figures. Then output
  `schedule_ci_reliability_loop`'s `response_text` exactly and stop.

## Plan

Track progress with the `update_plan` tool, not with headers or prose:

- On entry, before the first workflow tool call, call `update_plan` with the
  steps below verbatim, the first step `in_progress`, and a one-line
  `explanation` (this is not a diagnosis; no hypothesis table):
  `Scan this machine` / `Pick the repository` / `Analyze CI/CD reliability` /
  `Schedule the loop`.
- When the request or an Ask User answer already names the repository, omit the first two steps
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

### 3. Analyze CI/CD reliability

Call
`analyze_github_ci_reliability(owner="<owner>", repo="<repo>", compact=true)`
for the chosen repository. A saved report from today is reused; otherwise
this reads GitHub (a token is required). The tool paints the report. Do
not restate figures.

### 4. Schedule the loop

Call `schedule_ci_reliability_loop(owner="<owner>", repo="<repo>")` for the
analyzed repository. Do not pass `include_report`: the report was just shown.
The loop runs weekdays at 08:00 local time; the card states the schedule and
how to run it at another time, so do not ask about the cadence.
Output `response_text` verbatim and stop. Each later tick is the same
analytics report, not a CI code fix.
