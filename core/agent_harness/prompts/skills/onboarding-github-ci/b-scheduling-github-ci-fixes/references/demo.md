# Private scheduled-repair demo

Run only after the user accepted the demo lifecycle. The demo owns its own
repository, checkout, and loop; never touch the selected real repository.
The whole demo is bounded to **30 minutes** from fixture creation. On any
failure, record it and jump to cleanup (step 5) with the resources created so
far; skip deleting anything that was never created.

Expand these steps into the live plan with `update_plan`. Keep the working
style from the main skill: no narration between calls, no resource-record
writes after every step, one record write per step at most.

- [ ] Step 1. Create the fixture repository and regression branch.
- [ ] Step 2. Open the demo PR and confirm red CI.
- [ ] Step 3. Schedule the every-minute repair loop.
- [ ] Step 4. Observe one scheduled repair.
- [ ] Step 5. Save the report and delete all demo resources.
- [ ] Step 6. Reply with the result.

## 1. Create the fixture

Create a uniquely named private repository under the authenticated user with
`github_cli(args=["repo", "create", "<login>/opensre-ci-fix-demo-<suffix>", "--private"])`;
keep its full name and id from the result. Then one `shell_run` script in a
fresh temporary directory: a minimal Python project with a meaningful unit
test (a discount calculation with an asserted total) and a GitHub Actions
workflow running that test on pushes and pull requests; commit and push
`main`; create a demo branch with one real implementation bug (subtract the
discount instead of applying its fraction), push it. Write one resource-record
file outside the checkout with the repository name and id, checkout path, and
both SHAs. Do not weaken the test or workflow to change CI results.

## 2. Open the PR and confirm red CI

Open the PR with `github_cli` from the demo branch to `main`; the body says
this is an intentionally broken, disposable OpenSRE demonstration. Keep the
returned PR URL and number. Poll the head's checks with bounded `github_cli`
calls until the test check reports failure; pending or absent checks are not
red evidence. If Actions cannot run, record the setup failure and go to
step 5.

## 3. Schedule the repair loop

Create the loop with the main skill's entrypoint at the fastest cadence:

```text
slash_invoke(command="/loops", args=["add", "--name", "CI fix demo · <login>/<demo-repo>", "--cron", "* * * * *", "--mode", "agent", "--channel", "shell", "--prompt", "<tick prompt for the demo repository and its recorded checkout>"])
```

Record the loop id from the observation. Do not call `fix_github_pr_ci`
yourself to stand in for the scheduled repair.

## 4. Observe the scheduled repair

Read `slash_invoke(command="/loops", args=["show", "<demo-loop-id>"])` at most
once per minute. Stop at the first decisive outcome: a run whose result names
the PR with a repair commit and the repaired head's checks green, a named
blocker repeated across two consecutive ticks, or the 30-minute deadline.
Keep the deciding run id, fire time, and repaired SHA.

## 5. Save and clean up

Stop the loop with `slash_invoke(command="/loops", args=["stop", "<demo-loop-id>"])`.
If the latest run is still in progress, re-read `/loops show` until it
finishes — never delete resources a running tick may still push from. Then
one `shell_run` writes the report outside the checkout: PR link, baseline /
failing / repaired SHAs, observed run ids, outcome, and the exact blocker on
failure. Save before deleting the loop; deletion removes run history. Delete
in order, verifying each identity against the resource record:

1. `github_cli(args=["repo", "delete", "<recorded-owner/repo>", "--yes"])`
2. the recorded checkout directory (never a guessed or wildcard path)
3. `slash_invoke(command="/loops", args=["delete", "<demo-loop-id>"])`

A failed deletion is recorded and reported as remaining, not retried blindly
and never claimed as removed.

## 6. Reply

Five or six lines: repair outcome (succeeded, failed, or timed out), the PR as
`[#N](https://github.com/<owner>/<repo>/pull/N)`, the repaired SHA when there
is one, how many scheduled ticks ran, which resources were removed or remain,
and the saved report path. Then return to the main skill's step 4 when a real
repository was chosen.
