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
  version: "1.7"

---

# CI/CD analytics

Produce a CI/CD reliability report for one repository from raw GitHub
Actions records: the metrics table under "Required report" plus an
estimate of CI waiting time on merged pull requests. Use a 30-day window
unless the request specifies another period.

## Plan
Use the `update_plan` tool to track CI/CD Reliability Progress:

- [ ] Step 1. Scan local repositories with scan_local_git_workspace.
- [ ] Step 2. Select a repository using ask_user_choice.
- [ ] Step 3. Read the metric and benchmark references; collect complete 30-day workflow, rerun, and merged-PR history.
- [ ] Step 4. Calculate all required metrics and validate coverage, denominators, and consistency.
- [ ] Step 5. Display the metrics table, benchmark comparison, developer-impact estimates, and limitations.
- [ ] Step 6. After displaying the report, use ask_user_choice to offer scheduling, Slack setup, or finish.


## Workflow

### 1. Scan this machine

When the request or an Ask User answer already names the repository, skip
to step 3. Otherwise call `scan_local_git_workspace()` with no arguments.

### 2. Pick the repository

Call `ask_user_choice` with the title `Which repository should I analyze?`.
Offer up to three scanned repositories that have GitHub Actions workflows
as `<owner/repo>`, then `Tracer-Cloud/opensre` as the example. End the turn;
the answer arrives as the next user message.

### 3. Collect the repository's Actions history

Read both references before collecting:

- [Metrics](references/metrics.md): populations, formulas, and validation,
  via `skill_view(name="cicd-analytics-demo", reference="metrics")`.
- [Benchmarks](references/benchmarks.md): the comparison values and their
  limits, via `skill_view(name="cicd-analytics-demo", reference="benchmarks")`.

Resolve the default branch and fix the UTC window. Use `github_cli` for
focused read-only queries and `execute_python_code` for paginated GitHub
API collection; enable `allow_network`, use the injected `GITHUB_TOKEN`,
and keep credentials out of output. Collect what `metrics.md` defines:
default-branch runs, PR runs, prior attempts, merged PR identities and
authors, and boundary history. Paginate to exhaustion and split queries
that hit API caps. `list_github_actions_workflow_runs(head_sha=...)` and
its `history_fully_fetched` flag describe one commit, not the window.

Each `execute_python_code` call runs in a fresh process: carry the records
or sufficient intermediate state explicitly between calls, and keep run
IDs, workflow IDs, PR and head-repository identities, SHAs, timestamps,
conclusions, and evidence URLs through to calculation.

### 4. Calculate the metrics and check the invariants

Compute every metric in `metrics.md` with `execute_python_code` and assert
its validation invariants in the same code. Return compact results: each
metric with numerator, denominator, and unit; coverage gaps; assumptions;
evidence links. Recover truncated responses before using the affected
metric. Trace representative failures and waits to their source records;
correct collection or calculation errors and rerun the affected step.

### 5. Present the checked report and offer the next step

Check each table cell against a calculation result or the benchmark
reference. Then, in one response: write the required report as the message
text, mark this step `completed`, and call `ask_user_choice` with the title
`What would you like to do next?` and these options:

- Schedule local loops
- Slack setup
- Finish

The menu ends the turn. A reply without the menu ends the turn with no
follow-up, and a report written in an earlier response is discarded, so
the report and the menu always travel in the same response.

## Required report

Identify the repository, default branch, UTC window, and coverage. Render
this table, replacing every placeholder with a calculated value or a
benchmark from the reference:

| Metric | <owner/repo> | langchain-ai/langchain | anomalyco/opencode |
|---|---:|---:|---:|
| Red time on main | <hours and % of window> | <benchmark> | <benchmark> |
| Mean time to green | <hours> | <benchmark> | <benchmark> |
| CI-caused failure rate | <% of PR workflow runs> | <benchmark> | <benchmark> |
| Slowest normal run | <minutes and workflow> | <benchmark> | <benchmark> |
| PR failure rate | <% of PR workflow runs> | <benchmark> | <benchmark> |

Below the table: execution and failure counts, failure classifications,
breakage count, affected merged PRs and authors, estimated working hours
waiting for CI with its working-hours assumption, and run or PR links for
significant findings. Caption the benchmark date and window.

Keep every row. Use `N/A — <reason>` when a measure has no applicable
population and `Unavailable — <missing evidence>` when collection cannot
supply it. When a blocker prevents measuring an in-scope metric, label the
report partial and name the blocker. For missing GitHub credentials, give
`opensre integrations setup github`.

## Follow up

Ask the user what to do next using the ask_user tool:

- **Schedule local loops:** revise the plan with
  `Schedule the weekday report` / `Confirm the schedule`. Use the generic
  `/loops add` command through `slash_invoke` with
  `--channel interactive_shell`
- **Slack setup:** load `slack-handoff` with `skill_view` and follow its
  plan.
- **Finish:** acknowledge in one line and conclude.
