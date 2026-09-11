# CI/CD reliability calculations

Use these definitions to compute the report from raw GitHub records.
Return metric values together with their numerators, denominators, units,
coverage status, and evidence links.

## Populations and coverage

Fix `[start, end)` in UTC. Count unique workflow run IDs created in that
window on the default branch or for `pull_request` / `pull_request_target`
events. Keep the two populations separate and deduplicate their union for
`executions`. `pr_executions` counts PR workflow runs, not pull requests.

Use outcomes observed by `end`. Eligible runs have a latest conclusion of
`success`, `failure`, `timed_out`, `startup_failure`, or `action_required`.
The last four are failures. Report excluded pending, cancelled, skipped,
and neutral runs separately; their absence from rates does not mean success.

Fetch earlier attempts for rerun IDs. Deduplicate attempts by
`(run_id, run_attempt)` and keep them as history, not extra executions.
A later attempt number alone does not establish an earlier failure.
Use stable workflow IDs and PR number plus head repository when matching
history; a green Security workflow cannot recover a failed CI workflow.

Merged-PR impact covers PRs merged within the window. Associate runs with
their actual PRs and authors, including forks and reused branch names.
Fetch pre-window history needed to establish initial branch state and
relevant PR waits; boundary records do not add to in-window execution counts.

Record pagination completion, fetched populations, excluded outcomes, and
any missing attempts, PR associations, or boundary state. Determine which
metrics each gap affects. Commit-level history completeness does not prove
window completeness, and an empty successful query differs from a failed one.

## Failure counts and rates

`pr_failures` counts each eligible PR run once if any fetched attempt failed,
including a run whose latest attempt passed. Classify each failing run by
the first subsequent success in its workflow and PR history, as of `end`:

- `reliability_failures`: an observed failure followed by success on the
  same SHA, within that run's attempts or a later run.
- `source_failures`: the first subsequent success uses a different SHA.
- `unresolved_failures`: no subsequent success was observed.

These are outcome-based categories. The report's **CI-caused** label means
the same-SHA recovery proxy; it does not establish root cause. A change of
SHA before success likewise does not prove that source code caused failure.
Missing history leaves classification unavailable rather than unresolved.

Calculate `pr_failure_rate = 100 * pr_failures / pr_executions` and
`ci_failure_rate = 100 * reliability_failures / pr_executions`.
Also report reliability failures as a share of failed PR runs. A zero
denominator is `N/A`, not a zero rate.

## Normal workflow duration

For each workflow, `normal_minutes` is the median execution duration of
successful first attempts in the counted populations. Use attempt start
and completion times; fetch job completion times if necessary. If using
`updated_at` as a completion proxy, disclose that approximation.

The **Slowest normal run** is the largest of these per-workflow medians,
with its workflow name and sample count. A workflow without successful
first attempts has no measured baseline; disclose missing baselines.

## Default-branch red time and recovery

Use default-branch `push` runs to reconstruct the observed branch state
in time order. Track which commit is current as well as its workflow
attempts; a late result for a superseded commit must not change branch state.
State the use of run creation times as a push-time proxy if applicable.

An observed failure on the current commit starts a red interval. Close it
when that commit, or a successor, has completed all its applicable workflows
successfully. Pending or excluded outcomes do not establish green. Once a
successor is fully green, a workflow that ran only on an older commit does
not keep the branch red. Use attempt history to capture red-then-green
reruns even when the latest run listing is entirely successful.

Carry known red state across `start`; clip intervals to the window and
union overlaps. Sum their hours as `red_hours`; **Red time on main** is
`red_hours` and `100 * red_hours / window_hours`. Count newly opened red
intervals as breakages, identifying any already open at `start` separately.

`mean_recovery_hours` (**Mean time to green**) is the mean full duration of
outages recovered inside the window whose starts are known. Exclude ongoing
outages and disclose their count. No recovered outages means `N/A`.
Unknown initial state prevents a complete red-time claim; report any known
intervals as partial evidence rather than assuming the branch began green.

## Estimated developer impact

For each merged PR commit with an observed same-SHA recovery:

1. Set expected green time to its earliest run queue time plus the slowest
   normal duration among its workflows.
2. Set observed green time to the last of those workflows' first successful
   completions. Missing success or baseline makes that commit's estimate
   unavailable, not zero.
3. Bound the delay from expected green to observed green by the next commit's
   push time, the PR merge time, and the reporting window. Discard non-positive
   intervals. Union overlapping intervals within each PR.

Report affected merged PRs (`blocked_prs`), their distinct known authors
(`developers_affected`), and their summed PR waiting hours. These waits are
an estimate of CI-related delay, not proof that merging was blocked.

For estimated developer working time, union intervals across each author's
PRs before summing across authors. Intersect with the user's working schedule,
or explicitly assume weekdays 09:00–18:00 UTC. Return
`blocked_working_minutes` and divide by 60 for working hours. Disclose waits
with unknown authors separately. Do not equate this estimate with measured
idle time, lost productivity, or monetary cost.

## Validation invariants

- Unique counts reconcile: `reliability + source + unresolved = pr_failures`
  when classification coverage is complete; `pr_failures <= pr_executions`.
- Numerators and denominators share the same population and window. Rates
  are between 0% and 100%; `ci_failure_rate <= pr_failure_rate`.
- `0 <= red_hours <= window_hours`; overlapping workflows never multiply
  branch downtime. Ongoing outages do not enter mean recovery.
- Waits end no later than the next push, merge, or window end. Per-author
  working intervals are disjoint and lie within the stated working schedule.
- Every affected PR must link to an observed same-SHA recovery. With complete
  relevant history and no such recoveries, affected PRs, affected authors,
  and CI-caused waiting time are all zero. Source-failure recovery time and
  main-branch downtime are not inputs to developer blocked time.
- An unspecified working schedule uses the documented default; it is not
  missing evidence. Missing PR associations or recovery timestamps are.
- A claimed zero has complete supporting coverage. Each unavailable metric
  names its missing input; each measured metric is traceable to source records.
