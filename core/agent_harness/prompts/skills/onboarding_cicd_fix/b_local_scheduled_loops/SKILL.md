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
  owner: Tracer Team
  usecases:
    - First-experience demo: schedule a weekday CI/CD reliability agent for one repo
    - Watch CI reliability over time without a one-shot analytics report
    - Recurring weekday CI reliability check delivered to the shell inbox
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (optional; a named repository also works)
  type: report
  version: "1.0"
tools:
  - scan_local_git_workspace
  - schedule_ci_reliability_loop
  - ask_user_choice
---

# CI/CD reliability agent

Schedule a recurring CI/CD reliability check for one repository, delivered to
the shell inbox.

## When to use

- The user picked "Set up an agent that improves CI/CD reliability over time"
  from the startup demo menu (option B), or asks to watch CI reliability over
  time, schedule a weekday reliability check, or keep an agent on one repo's CI.
- The analytics demo's next-step menu offered that same option after a report.

## Related workflows

- A one-shot CI/CD performance report. Use `cicd-analytics-demo`.
- Listing checks that are failing right now. Use `github-ci-health`.
- Fixing a failing check. Use `github-ci-fix`.

## Workflow rules

- Never run `gh`, `git`, or `shell_run` for this flow.
- If a tool reports a missing GitHub token, say `opensre integrations setup github`
  and stop. Do not fall back to another data source.
- Decision points use `ask_user_choice` with the exact option texts below.
  End the turn after calling it; the answer arrives as the next user message.
- Ask each question once. When the answer arrives, continue immediately.
- Output `schedule_ci_reliability_loop`'s `response_text` exactly and stop.
  The loop delivers to this shell's inbox and never posts to Slack.

## Workflow

When the request already names the repository, start at step 3.

### 1. Scan this machine

Call `scan_local_git_workspace()` with no arguments. Say in one sentence
what was found, using `summary` from the result.

### 2. Pick the repository

From the scan result, candidates are repositories with a `github` name and
`has_workflows` true, ordered by `commits`. Then call `ask_user_choice`
with title `Which repository should the agent watch?` and options, in this
order:

- up to three candidates as `<owner/repo> (<commits> commits, CI configured)`
- `Use the open-source example repository (Tracer-Cloud/opensre)`

If there are no candidates, offer only the example repository and say why.
Wait for the answer.

### 3. Pick when it runs

Call `ask_user_choice` with title `When should it run?` and options:

- `Weekdays at 08:00 (recommended)`
- `Every day at 08:00`

Wait for the answer.

### 4. Schedule the loop

Call `schedule_ci_reliability_loop(owner="<owner>", repo="<repo>")` for
weekdays, or pass `weekdays=false` when they chose every day. Output
`response_text` verbatim and stop.

## Progress updates

Before every numbered step's tool calls, emit this exact header format as
assistant text in the same response, followed by one short status sentence:

```text
### [n/4] <step name>
<One-sentence status.>
```

Never start tool calls for a new step without its header.
