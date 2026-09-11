---
name: cicd-analytics-demo
description: >-
  Computes a CI/CD metrics table from raw GitHub Actions records for one
  repository over the last 30 days, including failure rates and developer
  waiting time. Use for historical
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
  version: "1.11"

---

# CI/CD analytics

Produce a CI/CD reliability report for one repository from raw GitHub
Actions records, including an estimate of CI waiting time on merged pull
requests. Use a 30-day window unless the request specifies another period.

## Plan

After reading this skill, use `update_plan` to create or revise the live
CI/CD Reliability Progress plan using the five numbered workflow headings below as its steps. 

- [ ] Step 1. Scan local repositories with scan_local_git_workspace.
- [ ] Step 2. Select a repository using ask_user_choice.
- [ ] Step 3. Collect and compute the 30-day metrics with analyze_github_ci_reliability.
- [ ] Step 4. Display a metrics table as mark down text
- [ ] Step 5. Use ask_user_choice to offer scheduling, Slack setup, or finish.

## Workflow

### 1. Scan this machine

Call `scan_local_git_workspace()` with no arguments.

Complete when the scan returns repository candidates, including an empty
result; the example repository remains available in step 2.

### 2. Pick the repository

Call `ask_user_choice` with the title `Which repository should I analyze?`.
Offer up to 5 scanned repositories that have GitHub Actions workflows
as `<owner/repo>`, then `Tracer-Cloud/opensre` as an example option. 

End the turn after the user has provided an answer to the `ask_user_choice` tool and the answer arrives as the next user message.

Complete when the answer identifies the repository. Resume at step 3 with
that repository.

### 3. Collect and compute the metrics

Call `analyze_github_ci_reliability(owner="<owner>", repo="<repo>", days=30, compact=true)`.
It reads the whole window of Actions history (default-branch runs, PR runs,
rerun attempts, merged PRs), computes every metric in the report, and returns
`key_results`, `coverage_notices`, and `benchmarks`. Do not paginate the REST
API or run `execute_python_code` yourself.

If the tool reports a missing token, tell the user to run
`opensre integrations setup github` and carry that blocker into step 4 as a
coverage gap.

Metric definitions live in [Metrics](references/metrics.md)
(`skill_view(name="cicd-analytics-demo", reference="metrics")`); read it only
when the user asks how a figure is defined.

Complete when the tool returns success with key results, or a named blocker.

### 4. Display a metrics table as mark down text

Read [Benchmarks](references/benchmarks.md) via
`skill_view(name="cicd-analytics-demo", reference="benchmarks")` now for
the comparison values and their interpretation limits. Check each table
cell against a calculation result or this reference.

Prepare the report below for delivery. 

#### Report format
After calculating the metrics, respond directly with the report as a Markdown table. Writing that response delivers the report. Do not substitute a plan update or next-step menu for it.

Identify the repository, default branch, UTC window, and coverage. Render
this table as text, replacing every placeholder with a calculated value or a
benchmark from the reference:

```
Developer impact: 
- xx developer-hours spent waiting on CI across xx developers.
- Most affected developer: up to xx h/week waiting on CI.
- xx% of PR runs failed, creating substantial retry and investigation overhead.

Compared with langchain-ai/langchain and anomalyco/opencode:

| Metric | <owner/repo> | langchain-ai/langchain | anomalyco/opencode |
|---|---:|---:|---:|
| Red time on main | <hours and % of window> | <benchmark> | <benchmark> |
| Mean time to green | <hours> | <benchmark> | <benchmark> |
| CI-caused failure rate | <% of PR workflow runs> | <benchmark> | <benchmark> |
| Slowest normal run | <minutes and workflow> | <benchmark> | <benchmark> |
| PR failure rate | <% of PR workflow runs> | <benchmark> | <benchmark> |

What insights stand out: 
- CI-caused failures account for x.x% of all PR runs, roughly x.x-x.x× higher than the comparison repositories.
```

### 5. Offer the next step

Call `ask_user_choice` with the title
`What would you like to do next?` and these options:

- Schedule local loops
- Slack setup
- Finish

Complete when the menu is offered with the report. The user's answer
arrives in the next turn; follow only the selected branch:

- **Schedule local loops:** revise the plan with
  `Schedule the weekday report` / `Confirm the schedule`. Use the generic
  `/loops add` command through `slash_invoke` with
  `--channel interactive_shell`.
- **Slack setup:** load `slack-handoff` with `skill_view` and follow its
  plan.
- **Finish:** acknowledge in one line and conclude.
