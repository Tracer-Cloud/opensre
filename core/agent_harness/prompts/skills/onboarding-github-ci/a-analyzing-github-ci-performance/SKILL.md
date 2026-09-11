---
name: analyzing-github-ci-performance
description: >-
  Computes a CI/CD metrics table from raw GitHub Actions records for one
  repository over the last 30 days, including failure rates and developer
  waiting time. Use for historical
  CI performance questions or the first-experience repository demo.
  For currently failing checks, use reporting-github-ci-failures.
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
    - Handing off to the weekday report loop (scheduling-github-ci-fixes) or Slack setup after the report
  requires:
    - GitHub token usable by OpenSRE with read access to the repository's Actions history
    - A local git checkout for the workspace scan (the example repository works without one)
  type: analytics
  version: "1.14"

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
- [ ] Step 4. Display a metrics table as Markdown text
- [ ] Step 5. Use ask_user_choice to offer scheduling, Slack setup, or finish.

## Workflow

### 1. Scan this machine

Call `scan_local_git_workspace()` with no arguments.

Complete when `scan_local_git_workspace` has returned in this turn, even
with an empty result; the example repository remains available in step 2.

### 2. Pick the repository

Call `ask_user_choice` with the title `Which repository should I analyze?`.
Offer up to 5 scanned repositories that have GitHub Actions workflows
as `<owner/repo>`, then `Tracer-Cloud/opensre` as an example option.
Offer the picker even when only one repository was found; a single scan
result is not a selection.

End the turn after calling `ask_user_choice`; the answer arrives as the
next user message.

Complete when the user's answer to `ask_user_choice` has arrived as a
message. Resume at step 3 with that repository.

### 3. Collect and compute the metrics

Call `analyze_github_ci_reliability(owner="<owner>", repo="<repo>", days=30)`.
It reads the whole window of Actions history (default-branch runs, PR runs,
rerun attempts, merged PRs), computes every metric in the report, and returns
figures only: `headline`, `key_results`, `comparison_figures` (the repository's
column of the comparison table), `benchmarks` (the peer columns),
`coverage_notices`, and the raw counts. It renders nothing; the report below
is yours to write. Do not paginate the REST API or run `execute_python_code`
yourself.

If the tool reports a missing token, tell the user to run
`opensre integrations setup github` and carry that blocker into step 4 as a
coverage gap.

Metric definitions live in [Metrics](references/metrics.md)
(`skill_view(name="analyzing-github-ci-performance", reference="metrics")`); read it only
when the user asks how a figure is defined.

Complete when `analyze_github_ci_reliability` has returned in this turn,
either with `key_results` or with a named blocker.

### 4. Display a metrics table as Markdown text

Read [Benchmarks](references/benchmarks.md) via
`skill_view(name="analyzing-github-ci-performance", reference="benchmarks")` now for
the comparison values and their interpretation limits. Check each table
cell against a calculation result or this reference.

If a cell has no source in the step 3 result or this reference, return to
step 3: reread `coverage_notices` for the named gap, and if the analysis did
not return success, run it again once. A cell still without a source is
`n/a` with the gap stated under the table; never estimate it.

Prepare the report below for delivery.

#### Report format
After calculating the metrics, respond directly with the report as a Markdown table. Writing that response delivers the report.

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

Complete when `skill_view` has returned the benchmarks reference in this
turn and the assistant reply contains the table.

### 5. Offer the next step

Call `ask_user_choice` with the title
`What would you like to do next?` and these options:

- Schedule local loops
- Slack setup
- Finish

Complete when the `ask_user_choice` call for this menu has returned in
this turn. The user's answer arrives in the next turn. Each branch except
`Finish` is owned by a sibling skill: load it with `skill_view` and follow
its plan; do not reimplement its steps here.

- **Schedule local loops:** call `skill_view(name="scheduling-github-ci-fixes")`
  and follow that skill. The repository is already chosen and analyzed in
  this session, so its plan omits the scan and repository-pick steps and
  its analyze step reuses today's saved report.
- **Slack setup:** call `skill_view(name="connecting-slack")` and follow that
  skill.
- **Finish:** acknowledge in one line and conclude.
