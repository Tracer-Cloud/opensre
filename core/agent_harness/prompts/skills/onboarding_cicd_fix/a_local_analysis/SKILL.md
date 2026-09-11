---
name: cicd-analytics-demo
description: >-
  Analyzes one repository’s CI/CD reliability over the last 30 days,
  including failure rates and developer time blocked. Use for historical
  CI performance questions or the first-experience repository demo.
  For currently failing checks, use github-ci-health.
getting_started: Explore a repo and analyze its CI/CD performance (recommended)
demo_order: 1
metadata:
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-09-11
  usecases:
    - First-experience demo: scan the machine, pick a repository, analyze its CI/CD
    - CI/CD reliability KPIs for one repository over the last 30 days
    - Developer time blocked by unreliable CI, estimated bottom-up per merged PR
    - Scheduling the weekday CI reliability report to this shell
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (optional; a named repository also works)
  type: analytics
  version: "1.4"
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

Analyze one repository’s CI/CD reliability over the last 30 days, then
offer a CICD fix scheduled loop or Slack agent setup.

## Workflow

1. **Choose a repository.** Use the repository named by the user.
   Otherwise call `scan_local_git_workspace()` and give one sentence
   using its `summary`. The host opens the repository menu.

2. **Analyze.** Call
   `analyze_github_ci_reliability(owner="<owner>", repo="<repo>", compact=true)`.
   After a successful report, the host opens the next-step menu.

3. **Follow the selection.** Use the analyzed repository for the selected
   branch below. A next-step menu answer resumes here without rerunning
   discovery or analysis.

Whenever the host queues a menu, update the plan and end the turn.
The answer arrives in the next user message; the host owns these questions.

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

```
CI/CD Reliability Progress:
- [ ] Step 1: Find local repositories and summarize recent activity
- [ ] Step 2: Select the repository for a 30-day reliability analysis
- [ ] Step 3: Collect default-branch runs, PR runs, and merged pull requests
- [ ] Step 4: Examine rerun attempts and classify CI-caused, source, and unresolved failures
- [ ] Step 5: Calculate execution counts, PR failure rates, and normal workflow durations
- [ ] Step 6: Measure default-branch breakages, downtime, and recovery time
- [ ] Step 7: Estimate developer working time blocked by CI on merged pull requests
- [ ] Step 8: Display the reliability report and comparison with supplied benchmarks
- [ ] Step 9: Check the report’s scope and identify any data coverage limitations
- [ ] Step 10: Choose whether to schedule reports, set up Slack, or finish
```

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
`analyze_github_ci_reliability(owner="<owner>", repo="<repo>", compact=true)`
for the chosen repository. A saved report from today is reused; otherwise
this reads GitHub (a token is required). The tool paints the report — the
cost sentence first, then key results and the comparison against shipped
`langchain-ai/langchain` and `anomalyco/opencode` figures. Do not output
`headline`, and do not call the tool again for benchmarks.

### Follow-up actions

**Recurring report:** Call
`schedule_ci_reliability_loop(owner="<owner>", repo="<repo>")`,
omitting `include_report`. Return its `response_text` verbatim and finish.
This schedules weekday reports covering seven days.
`/loops service install` keeps them running when the shell is closed.

**Slack setup:** Call `cli_exec` with `integrations verify slack`.
If unconfigured, queue `/integrations setup slack` through `slash_invoke`
and end the turn so the terminal wizard can run.
If connected, confirm that and explain that the user can mention OpenSRE
in a channel or DM it to hand off a chore.
Do not send Slack messages.

**Exit:** Acknowledge in one line and finish.

## Workflow rules

- Never run `gh`, `git`, or `shell_run` for this flow; the scan and
  analysis tools own discovery and analysis end to end. Use `cli_exec` only
  to verify Slack in step 4; queue setup with `slash_invoke`
  (`/integrations setup slack`), never `cli_exec`.
- The tools render the workspace chart, repository list, and reports; do
  not add an unsolicited recap. Answer explicit follow-up questions using
  their results.
- If a tool reports a missing GitHub token, say the one command the user runs
  (`opensre integrations setup github`) and offer to continue afterwards. Do
  not fall back to a different data source.
- If the analysis result is not successful for any other reason, say why in
  one line and stop; the next-step menu only opens after a report.
- The host opens the repository menu after `scan_local_git_workspace` and the
  next-step menu after `analyze_github_ci_reliability`. Do not call
  `ask_user_choice` for those two questions. End the turn when a menu is
  queued; the answer arrives as the next user message.
- Which question was answered decides the next step. An answer to `Which
  repository should I analyze?` (or a repository named in the request) is the
  repository: go straight to step 3. An answer to `What would you like to do
  next?` picks a branch under step 4: the analysis is already done, do not run
  it again, go straight to that branch and call only its tool.
