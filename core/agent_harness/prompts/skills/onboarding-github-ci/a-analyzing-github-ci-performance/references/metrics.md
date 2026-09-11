# CI/CD reliability metrics

The report's figures, in priority order, with the payload field to quote.
Answer "what does this mean" and "what should we fix first" from this list
and the tool's numbers; never recompute or re-derive a figure.

1. **Red time on main** (`red_hours`, `outages`): hours the default branch had
   at least one red workflow. Top-line health.
2. **Mean time to recovery** (`mean_recovery_hours`): average length of a red
   period until green. Two repos with equal red time can recover very
   differently.
3. **CI-caused failures** (`reliability_failures` of `pr_failures`): the same
   commit passed later, so CI, not the code, failed. The developer-friction
   signal; the tool says "CI-caused", not "flaky".
4. **Normal duration** (`workflows[].normal_minutes`): per-workflow run time
   when everything passes. The wait developers pay even on a green day.
5. **Developer blocked time** (`merged_pr_branches`, `blocked_prs`,
   `blocked_working_minutes`): merged PRs held past their expected green time
   by CI-caused failures. Ties CI instability to delivery, not just to infra
   statistics.
