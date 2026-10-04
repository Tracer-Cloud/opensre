---
name: analyzing-github-ci-performance
description: >-
  Computes a CI/CD metrics table from raw GitHub Actions records for one repository over the last 30 days,
  including failure rates and developer waiting time. Use for historical CI performance questions or the
  first-experience repository demo. For currently failing checks, use reporting-github-ci-failures.
getting_started: Analyze & improve a repo (recommended)
demo_order: 1
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-10-04
  usecases:
  - For repository maintainers analyzing CI reliability over the previous 30 days.
  - For engineering teams assessing estimated developer waiting time and failure patterns.
  - For new users selecting a local or example repository for a CI performance demonstration.
  requires:
  - GitHub authentication with read access to the repository's Actions history.
  - The analyze_github_ci_reliability and scan_local_git_workspace tools.
  - For local discovery, a local Git checkout; the example repository does not require one.
  version: '1.23'
---

# CI/CD analytics

Give the user a CI/CD reliability report for one repository from raw GitHub Actions records, including an estimate of CI waiting time on merged pull requests.

Use a 30-day window unless the request specifies another period.

## Plan

After reading this skill, use `update_plan` to create or revise the live
CI/CD Reliability Progress plan using the five numbered workflow headings
below as its steps. Mark a step `in_progress` or `completed` in the same response as
that step's tool call.

Keep showing the table and offering the next step as separate plan items; update statuses as each step's completion condition is met.

Mark step 4 with `deliverable: true` in every `update_plan` call: its work is
the reply itself, and without that flag the host treats a text-only reply
before the plan is settled as a premature stop and does not show it.

- [ ] Step 1. Scan local repositories with scan_local_git_workspace.
- [ ] Step 2. Select a repository using ask_user_choice.
- [ ] Step 3. Collect the 30-day metrics with analyze_github_ci_reliability and prepare the metrics table from its result.
- [ ] Step 4. Show the metrics table as a text-only reply.
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

If the tool reports a missing token, tell the user to run `opensre integrations setup github` and carry that blocker into the table as a coverage gap.

Metric definitions live in [Metrics](references/metrics.md)
(`skill_view(name="analyzing-github-ci-performance", reference="metrics")`); read it only
when the user asks how a figure is defined.

#### Prepare the metrics table as Markdown text

Take the peer columns from the analysis result's `benchmarks`, measured on
`benchmarks_measured_on` over 30 days, and caption the table with that date
and window: they are historical context, not a ranking. Do not call
`skill_view` for them. Check each table cell against the analysis result.

If a cell has no source in the analysis result, return to
the call above: reread `coverage_notices` for the named gap, and if the analysis did
not return success, run it again once. A cell still without a source is
`n/a` with the gap stated under the table; never estimate it.

Prepare the report below for delivery in step 4.

#### Report format

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

Complete when `analyze_github_ci_reliability` has returned in this turn,
either with `key_results` or with a named blocker, and every table cell has
a source in its result or is `n/a` with its gap stated.

### 4. Show the metrics table

Reply with the step 3 report as Markdown text and nothing else; call no
tool in that message. The host paints that reply and then continues the
plan with step 5 — do not repeat the report afterwards.

Complete when the assistant reply containing the table has been shown.

### 5. Offer the next step

Call `ask_user_choice` with the title `See a failing check get fixed?`, `allow_custom` false, and this note: `A private demo repository, so the one in the report stays untouched. You watch one check go from red to green.`

Options:
- Watch it fix a failing PR in a demo repo (under 5 min)
- Run the same fix in OpenSRE managed service and close your laptop
- Not now

Complete when the `ask_user_choice` call for this menu has returned in this turn. The user's answer arrives in the next turn. Each branch except `Stop at the report` is owned by a sibling skill: load it with `skill_view` and follow its plan; do not reimplement its steps here.

- **Watch it fix a failing PR in a demo repo (under 5 min):** call `skill_view(name="scheduling-github-ci-repairs")`
  and follow that skill. The repository is already chosen and analyzed in
  this session, so its plan omits the scan and repository-pick steps and
  its analyze step reuses today's saved report.
- **Run the same fix in OpenSRE managed service and close your laptop:** call `skill_view(name="delegating-github-ci-repairs")`
  and follow that skill.
- **Not now:** acknowledge in one line and conclude.