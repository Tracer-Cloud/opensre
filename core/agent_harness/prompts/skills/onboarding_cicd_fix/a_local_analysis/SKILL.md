---
name: cicd-analytics-demo
description: >-
  CI/CD performance and reliability analytics for one repository over the
  last 30 days: executions, PR failure rate, CI-caused vs source failures,
  developer time blocked, default-branch red time, via
  analyze_github_ci_reliability; also the first-experience demo that scans the
  machine and picks a repository first. Use for "analyze <repo> CI/CD
  performance", "how reliable is our CI", "what does flaky CI cost us". Not for
  listing currently failing checks (github-ci-health). Multi-step; load before
  acting.
getting_started: Explore a repo and analyze its CI/CD performance (recommended)
demo_order: 1
metadata:
  owner: Tracer Team
  usecases:
    - First-experience demo: scan the machine, pick a repository, analyze its CI/CD
    - CI/CD reliability KPIs for one repository over the last 30 days
    - Developer time blocked by unreliable CI, estimated bottom-up per merged PR
    - Scheduling the weekday CI reliability report to this shell
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (optional; a named repository also works)
  type: analytics
  version: "1.1"
tools:
  - scan_local_git_workspace
  - analyze_github_ci_reliability
  - schedule_ci_reliability_loop
  - cli_exec
  - slash_invoke
  - ask_user_choice
references:
  - common/numbers_from_tools.md
  - common/ask_once.md
  - common/progress.md
after_tool:
  - after: scan_local_git_workspace
    tool: ask_user_choice
    args:
      title: Which repository should I analyze?
    options_from: local_git_scan_repos
    options_extra:
      - Use the open-source example repository (Tracer-Cloud/opensre)
  - after: analyze_github_ci_reliability
    tool: ask_user_choice
    args:
      title: What would you like to do next?
      options:
        - Set up an agent that improves CI/CD reliability over time
        - Connect OpenSRE to Slack and hand off DevOps chores for your team
        - Exit demo
---

# CI/CD analytics demo

Analyze one repository's CI/CD reliability, then offer a recurring check or
Slack setup.

## When to use

- The user picked "Explore a repo and analyze its CI/CD performance (recommended)"
  from the startup demo menu (option A), or asks to "run the CI/CD analytics
  demo", "analyze my repo's CI/CD performance", "show me how reliable our CI
  is", or "how much time does CI cost us".
- The user names a repository and asks for its CI/CD performance, reliability,
  failure rate, or downtime.

## Related workflows

- Fixing a failing check. Use `github-ci-fix`.
- Scheduling a recurring CI/CD reliability check with inbox reports. Use
  `cicd-reliability-agent`.
- Listing the checks that are failing right now. Use `github-ci-health`.

## Workflow rules

- Never run `gh`, `git`, or `shell_run` for this flow; the scan and
  analysis tools own discovery and analysis end to end and are read-only.
  The analysis itself is read-only: no Slack messages, no pushes, no
  issue writes. Use `cli_exec` only to verify Slack in step 4; queue setup
  with `slash_invoke` (`/integrations setup slack`), never `cli_exec`.
- The scan tool draws the workspace chart in the shell itself. Do not repeat
  the chart or the repository list as text; add one sentence at most.
- `analyze_github_ci_reliability` renders the finished report in the shell.
  Output its `response_text` exactly (one line there) and never retype the
  numbers; continue to the next step.
- If a tool reports a missing GitHub token, say the one command the user runs
  (`opensre integrations setup github`) and offer to continue afterwards. Do
  not fall back to a different data source.
- The host opens the repository menu after `scan_local_git_workspace` and the
  next-step menu after `analyze_github_ci_reliability`. Do not call
  `ask_user_choice` for those two questions. End the turn when a menu is
  queued; the answer arrives as the next user message.
- Which question was answered decides the next step. An answer to `Which
  repository should I analyze?` (or a repository named in the request) is the
  repository: go straight to step 3. An answer to `What would you like to do
  next?` picks a branch under step 4: the analysis is already done, do not run
  it again, go straight to that branch and call only its tool.

## Workflow

When the request already names the repository, start at step 3 and use
headers [3/4] and [4/4] only.

### 1. Scan this machine

Call `scan_local_git_workspace()` with no arguments. Say in one sentence
what was found, using `summary` from the result.

### 2. Pick the repository

The host opens `Which repository should I analyze?` after the scan: up to
three local repositories with GitHub Actions as
`<owner/repo> (<commits> commits, CI configured)`, then
`Use the open-source example repository (Tracer-Cloud/opensre)`. Wait for
the answer.

### 3. Analyze CI/CD reliability

Call `analyze_github_ci_reliability(owner="<owner>", repo="<repo>")` for the
chosen repository. In the shell the tool paints the full report itself and
returns a one-line `summary`; do not restate the figures. Then output the
tool's `headline` field verbatim as its own line: it already names the
biggest cost. Do not compute, convert, or reword any figure yourself, and
do not add a recap, bullet list, or "verified result" of your own after
the headline: the next assistant text is the step 4 header.

### 4. Offer what to do next

The host opens `What would you like to do next?` after the analysis with
these options:

- `Set up an agent that improves CI/CD reliability over time`
- `Connect OpenSRE to Slack and hand off DevOps chores for your team`
- `Exit demo`

Wait for the answer, then follow the selected option.

**Recurring check:** Call
`schedule_ci_reliability_loop(owner="<owner>", repo="<repo>")` for the
analyzed repository, output its `response_text` verbatim, and stop; it
schedules a weekday 08:00 local check that delivers to this shell's inbox
and never posts anywhere else. Each tick is deterministic (no model turn);
`/loops service install` keeps it running when no shell is open.

**Slack setup:** Call `cli_exec` with payload `integrations verify slack`.
If Slack is not configured, call `slash_invoke` with
`/integrations setup slack` and stop; that wizard needs a full terminal.
If Slack is already connected, say so. Then explain in two sentences how to
hand off a chore from Slack: mention OpenSRE in a channel or DM it.
Never post, reply, or send anything to Slack in this demo.

**Exit demo:** Reply with one line and stop.
