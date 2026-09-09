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
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-09-09
  usecases:
    - First-experience demo: scan the machine, pick a repository, analyze its CI/CD
    - CI/CD reliability KPIs for one repository over the last 30 days
    - Developer time blocked by unreliable CI, estimated bottom-up per merged PR
    - Scheduling the weekday CI reliability report to this shell
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (optional; a named repository also works)
  type: analytics
  version: "1.3"
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

## Workflow rules

- Never run `gh`, `git`, or `shell_run` for this flow; the scan and
  analysis tools own discovery and analysis end to end. Use `cli_exec` only
  to verify Slack in step 4; queue setup with `slash_invoke`
  (`/integrations setup slack`), never `cli_exec`.
- The tools paint the workspace chart and the finished report in the shell
  themselves; never restate their figures, repeat the repository list, or add
  a recap of your own.
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

## References

- **Metric definitions**: [references/metrics.md](references/metrics.md).
  Load it only when the user asks what a figure means or which metric to
  fix first, with `skill_view(name="cicd-analytics-demo", reference="metrics")`;
  answer from it and the tool's numbers. Do not load it during steps 1-4.
- **Benchmark table**: [references/benchmarks.md](references/benchmarks.md).
  Load it only when the user asks what a compared figure means. The analyze
  call already paints the comparison; do not load this to re-run peers.

## Plan

Track progress with the `update_plan` tool, not with headers or prose:

- On entry, before the first workflow tool call, call `update_plan` with the
  steps below verbatim, the first step `in_progress`, and a one-line
  `explanation` (this is not a diagnosis; no hypothesis table):
  `Scan this machine` / `Pick the repository` / `Analyze CI/CD reliability` /
  `Offer what to do next`.
- When the request already names the repository, the plan is only
  `Analyze CI/CD reliability` / `Offer what to do next` — omit the skipped
  steps instead of renumbering.
- After a step's tool results, call `update_plan` marking it `completed` and
  the next step `in_progress`, in the same response as the next step's tool
  calls. When the host queues a menu after a step's tool result, mark the
  step in that same response and end the turn.
- Do not narrate the plan or repeat step names in prose; the shell renders
  the checklist.

## Workflow

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

Call
`analyze_github_ci_reliability(owner="<owner>", repo="<repo>", include_benchmarks=true)`
for the chosen repository. The tool paints the report (key results first)
and the comparison table itself. Do not restate figures, do not output
`headline`, and do not call the tool again for benchmarks.

### 4. Offer what to do next

The host opens `What would you like to do next?` after the analysis with
these options:

- `Set up an agent that improves CI/CD reliability over time`
- `Connect OpenSRE to Slack and hand off DevOps chores for your team`
- `Exit demo`

Wait for the answer, then follow the selected option.

**Recurring check:** Call
`schedule_ci_reliability_loop(owner="<owner>", repo="<repo>")` for the
analyzed repository, output its `response_text` verbatim, and stop. Each
tick is deterministic (no model turn); `/loops service install` keeps it
running when no shell is open.

**Slack setup:** Call `cli_exec` with payload `integrations verify slack`.
If Slack is not configured, call `slash_invoke` with
`/integrations setup slack` and stop; that wizard needs a full terminal.
If Slack is already connected, say so. Then explain in two sentences how to
hand off a chore from Slack: mention OpenSRE in a channel or DM it.
Never post, reply, or send anything to Slack in this demo.

**Exit demo:** Reply with one line and stop.
