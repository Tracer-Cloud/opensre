# Proposed Bash scripts

Migration inventory for developers changing [this skill](SKILL.md). The files
below are proposed; implementing them and switching the workflow is follow-up
work. Keep them in a `scripts/` directory beside `SKILL.md`.

Use `.sh` entrypoints with `#!/usr/bin/env bash`. The
[Agent Skills specification](https://agentskills.io/specification#scripts)
allows host-supported scripting languages, including Bash and Python; Bash is
the choice for this migration.

## Tool replacements

| Proposed script | Replaces | Inputs | Result and responsibility |
| --- | --- | --- | --- |
| `scripts/scan-workspace.sh` | `scan_local_git_workspace` | Optional `--root` and `--days`; preserve current defaults. | Discover local Git repositories, fold duplicate clones, measure activity, and identify GitHub Actions workflows. Return the repository records and summary used by the repository picker, preserving scan bounds and truncation reporting. |
| `scripts/analyze-ci-reliability.sh` | `analyze_github_ci_reliability` | Required `--owner` and `--repo`; optional `--days` (default 30) and `--compact`. | Fetch GitHub history, calculate the report, save its snapshot, and return the report plus structured metrics and the shipped Airflow/FastAPI comparison. The demo uses `--compact`; render the finished report once. |
| `scripts/schedule-ci-reliability.sh` | `schedule_ci_reliability_loop` | Required `--owner` and `--repo`; optional `--time` and `--weekdays`. | Create or reuse the repository's weekday report through the OpenSRE scheduler. Preserve 08:00 local time by default, the rolling 7-day report, shell-inbox delivery, and deterministic report generation. Return the task ID, reuse status, next run, and confirmation text. |

## Implementation boundaries

Add supported OpenSRE CLI adapters for these operations before writing the
wrappers. Their command names and machine-readable output contracts still need
to be defined. Each Bash script should invoke its CLI adapter; the adapter
should call the owning service through its package API. Preserve the existing
domain implementations instead of translating the metrics into shell arithmetic
or routing the scripts back through the registered tools.

- Workspace discovery is owned by
  [`workspace_git_scan`](../../../../../../tools/system/workspace_git_scan/).
- GitHub collection, metrics, rendering, benchmarks, and snapshots are owned by
  [`ci_analytics`](../../../../../../integrations/github/tools/ci_analytics/).
  Keep pagination, rerun classification, overlapping outage intervals,
  merged-PR blocking time, working hours, and timezone handling there.
- Recurrence is owned by that package's
  [`loop.py`](../../../../../../integrations/github/tools/ci_analytics/loop.py)
  and the shared scheduler. The adapter must preserve its deterministic report
  builder and idempotent scheduling; generic prompt-based cron creation is not
  an equivalent replacement. Persist schedules through the scheduler API.

Declare Bash and the installed OpenSRE CLI as dependencies. Resolve credentials
through OpenSRE's GitHub integration. Give the host structured results on stdout,
progress on stderr, and a nonzero exit status on failure. The host should render
the workspace chart/report and open the matching menu only after success.

The current analysis implementation always fetches live GitHub data and saves
a snapshot. The skill's sentence about reusing today's report disagrees with
that implementation; reconcile it when migrating, keeping live analysis unless
the caching contract is deliberately changed. Scheduling after an analysis
must neither fetch again nor repeat that report.

## Workflow and migration gate

Preserve this sequence across user turns:

1. Run `scan-workspace.sh`, then let the host open the repository picker and
   wait for the user's answer. A request naming a repository skips this step.
2. Run `analyze-ci-reliability.sh` for that repository, then let the host open
   the next-step picker and wait for the user's answer.
3. Run `schedule-ci-reliability.sh` only when the user chooses the recurring
   report. Keep Slack setup and exit as their existing separate branches.

Before switching the skill, wire the script runner into its allowed tools and
map successful script results to the existing host-owned menu hooks. Update
the workflow instructions at the same time; they currently require the tools
listed above. Keep `ask_user_choice`, `update_plan`, and `slash_invoke` as host
capabilities rather than adding shell replacements.

Adapt the colocated [workflow test](test_workflow.py) to exercise real script
invocation and the real host hooks with external I/O stubbed. Pin the same
ordering, arguments, and user-choice pauses, including failure stopping the
next menu, then run the existing analytics and scheduling contract tests.
