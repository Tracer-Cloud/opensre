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
  version: "1.9"

---

# CI/CD analytics

Produce a CI/CD reliability report for one repository from raw GitHub Actions records: the metrics table under "Required report" plus an estimate of CI waiting time on merged pull requests. Use a 30-day window unless the request specifies another period.

## Plan

After reading this skill, use `update_plan` to create or revise the live
CI/CD Reliability Progress plan with the six phases below in order. Keep
reporting and follow-up as separate steps. Mark already-satisfied phases
`completed` and update statuses as work proceeds.

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

Resolve the default branch with `get_github_repository` and fix the UTC
window. Collect with `execute_python_code`: `allow_network` on, the token
read from `os.environ["GITHUB_TOKEN"]` inside the script, never printed.
Collect what `metrics.md` defines: default-branch runs, PR runs, prior
attempts, merged PR identities and authors, and boundary history. Paginate
the REST API to exhaustion and split queries that hit API caps.
`list_github_actions_workflow_runs(head_sha=...)` and its
`history_fully_fetched` flag describe one commit, not the window; do not
use that tool's page as the window population.

Records never pass through the model. Each `execute_python_code` call is a
fresh process with a 60-second cap, so the script saves what it fetched as
JSON under `Path(tempfile.gettempdir()) / "opensre"` (the sandbox's only
writable directory, e.g. `cicd-<owner>-<repo>.json`) and prints only
counts, the page range covered, and the file path. A later call loads and
extends that file; split by population or page range when one call would
run long. Never put fetched records in `inputs` or repeat them in the tool
call: a call whose arguments exceed the output budget arrives as `{}` and
fails with `missing required args: code`. Keep run IDs, workflow IDs, PR
and head-repository identities, SHAs, timestamps, conclusions, and evidence
URLs in the saved file for calculation.

### 4. Calculate the metrics and check the invariants

Load the saved file in `execute_python_code`, compute every metric in
`metrics.md`, and assert its validation invariants in the same code. Print
one compact JSON object under 2 KB: each metric with numerator,
denominator, and unit; coverage gaps; assumptions; the evidence links the
report cites. Rerun with a shorter output when a response is truncated
rather than using a partial value. Trace representative failures and waits
to their source records; correct collection or calculation errors and rerun
the affected step.

### 5. Present the checked report 

Check each table cell against a calculation result or the benchmark
reference. Then, in one response: write the required report as the message
text, mark this step `completed`.

### 6. Offer the next step
Call `ask_user_choice` with the title `What would you like to do next?` and these options:

- **Schedule local loops:** revise the plan with
  `Schedule the weekday report` / `Confirm the schedule`. Use the generic
  `/loops add` command through `slash_invoke` with
  `--channel interactive_shell`
- **Slack setup:** load `slack-handoff` with `skill_view` and follow its
  plan.
- **Finish:** acknowledge in one line and conclude.

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
