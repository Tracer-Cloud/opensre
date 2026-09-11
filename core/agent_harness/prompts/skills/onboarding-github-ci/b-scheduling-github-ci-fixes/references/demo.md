# Private scheduled-repair demo

Use only after the user accepts the demo lifecycle. The demo uses its own
repository, checkout, and loop. Never replace the selected real repository
with the demo repository.

## Plan

Expand the demo item in the live plan with this checklist using `update_plan`.
Perform one action at a time and update its status at the stated completion
condition. On any failure after resource creation, record the failure and
enter cleanup at step 9 with the identities already created. Unperformed
fixture/repair steps remain identified as skipped because of that failure.
Skip deletion of any resource that was never created. If stopping future
ticks or establishing quiescence fails, save the result in step 11 and go
directly to step 15; all deletion steps remain blocked.

- [ ] Step 1. Allocate and record a unique checkout with shell_run.
- [ ] Step 2. Create and record a private repository with github_cli.
- [ ] Step 3. Push a passing baseline fixture with shell_run.
- [ ] Step 4. Push one real code regression on a demo branch with shell_run.
- [ ] Step 5. Open the demo PR with github_cli.
- [ ] Step 6. Verify the expected failing CI check with github_cli.
- [ ] Step 7. Create the every-minute demo loop with slash_invoke.
- [ ] Step 8. Observe a scheduled repair outcome through runtime run history.
- [ ] Step 9. Stop future demo ticks with slash_invoke.
- [ ] Step 10. Wait for active demo repair work to finish through runtime status.
- [ ] Step 11. Save the demo result outside its checkout with shell_run.
- [ ] Step 12. Delete the recorded demo repository with github_cli.
- [ ] Step 13. Delete the recorded demo checkout with shell_run.
- [ ] Step 14. Delete the recorded demo loop with slash_invoke.
- [ ] Step 15. Display the saved result and cleanup status as Markdown text.

## Workflow

### 1. Allocate the disposable checkout

Use `shell_run` to create a fresh temporary directory and a separate durable
resource record. Record the returned absolute path; reuse no existing
directory. Choose a unique repository name under the authenticated user's
account. Names are labels, not evidence that this run owns a resource.

Complete when the new checkout and external resource-record path are recorded.

### 2. Create the private repository

Use `github_cli` to create the uniquely named private repository. Obtain its
full name, immutable ID, and private visibility from GitHub and save them in
the resource record. If the name is taken, choose another unused name; an
existing repository is never a disposable fixture for this run.

Complete when GitHub confirms the new repository's identity and visibility.

### 3. Push a passing baseline

Use `shell_run` in the recorded checkout to create a minimal Python project,
a meaningful unit test, and a GitHub Actions workflow that runs that test on
pull requests and pushes to the default branch. A discount calculation with
an asserted expected total is sufficient. Run the test successfully, commit
the baseline, and push it to the newly created repository. Record its SHA.

These setup commits belong only to the disposable fixture. All subsequent
repairs belong to `fix_github_pr_ci`; do not weaken the test or workflow to
make CI green.

Complete when the baseline test passes locally and its commit is on the remote.

### 4. Push the regression

Use `shell_run` to create a separate demo branch, introduce one deterministic
implementation bug, and push it. For the discount example, subtracting the
discount rather than applying its fraction makes the existing assertion fail.
Keep the test and workflow intact. Record the failing branch and head SHA.

Complete when the existing test reproduces the regression and the branch is
pushed. A deliberately failing workflow command is not a code regression.

### 5. Open the pull request

Use `github_cli` to open a PR from the recorded demo branch to its passing
baseline. Say in the PR description that this is an intentionally broken,
disposable OpenSRE demonstration. Record the returned PR URL and number.

Complete when the PR exists in the recorded private repository.

### 6. Verify red CI

Read the demo PR's checks through `github_cli` using bounded calls. Wait for
the actual test failure on the recorded head; a pending or absent check is
not red evidence. Record the failed check and run URL. If Actions cannot run
or setup cannot establish that failure, record a setup failure and proceed
to cleanup. Bound this setup wait to 30 minutes.

Complete when the expected test is confirmed failing on that head, or a named
setup failure is recorded. Only a confirmed failure proceeds to step 7.

### 7. Schedule the demonstration

Create the demo loop with the same entrypoint and repair policy as the real
loop, at the fastest supported cadence:

```text
slash_invoke(command="/loops", args=["add", "--name", "CI fix demo · <login>/<demo-repo>", "--cron", "* * * * *", "--channel", "shell", "--prompt", "<tick prompt for the demo repository and its recorded checkout>"])
```

Record its ID from the observation immediately. The demo has a 30-minute
observation deadline from schedule creation. The setup agent does not call
`fix_github_pr_ci` directly to stand in for the scheduled repair.

Complete when the observation records the loop ID, cron, and next scheduled
execution. Then follow step 8.

### 8. Observe the scheduled repair

Read the loop's run history with
`slash_invoke(command="/loops", args=["show", "<demo-loop-id>"])`, spaced
by the one-minute cadence, until a scheduled repair verifies green checks
for the current pushed head, a terminal blocker is returned, or the
30-minute demo deadline expires. Record the outcome with its scheduled fire time, PR head,
checks, and run URLs. A manual run or successfully delivered message is not
proof of scheduled repair. On a setup failure, retain that failure instead.

Complete when a scheduled repair is verified, the deadline expires, or a
named failure is recorded. Preserve that outcome for the saved report.

### 9. Stop future demo ticks

Call `slash_invoke(command="/loops", args=["stop", "<demo-loop-id>"])`
for the recorded demo loop. Skip the call when no loop was created. Leave
the real repository's loop unchanged.

Complete when future demo ticks are disabled or no demo loop exists. If
stopping fails, report a cleanup blocker and retain resources still in use.

### 10. Wait for active repair work

If the resource record establishes that no loop or repair was started, mark
this step satisfied without a status call. Otherwise read
`slash_invoke(command="/loops", args=["show", "<demo-loop-id>"])` until its
latest run is finished rather than in progress. Stopping future ticks does
not itself establish this; a tick already running completes on its own, so
wait for it before deleting resources it may still be pushing from.

Complete when no active operation can read, edit, or push from the demo
checkout. If this cannot be established, retain resources, save the failure
in step 11, skip all deletion steps (12–14), and report cleanup blocked in
step 15 instead of deleting active resources.

### 11. Save the result

Use `shell_run` to write the success or failure report outside the disposable
checkout. Include resource identities, failing and repaired SHAs when known,
scheduled execution evidence, CI run/check URLs, elapsed repair time, and
the exact unresolved blocker when repair failed or timed out. Preserve run
evidence before deleting the schedule, which removes its execution history.
Verify the file exists and is readable.

Complete when the report and resource record are saved. If saving fails,
retain the evidence-bearing resources, skip steps 12–14, and report incomplete
cleanup in step 15.

### 12. Delete the repository

After steps 9–11 succeed, verify that the recorded remote identity still
matches the private repository created by this run. Delete it with
`github_cli(args=["repo", "delete", "<recorded-owner/repo>", "--yes"])`.
Record successful deletion from the tool result. A missing permission or a
different repository ID leaves that resource recorded as remaining.

Complete when deletion is confirmed, the recorded resource is already gone,
or its cleanup blocker is recorded. A failed deletion is not successful cleanup.

### 13. Delete the checkout

Delete only the recorded, newly allocated demo directory using `shell_run`.
Verify its identity against the resource record and verify removal. Preserve
the report and resource record outside it. Never derive the deletion target
from a fixed home-directory name, repository label, or wildcard.

Complete when that checkout is gone or its cleanup blocker is recorded.

### 14. Delete the demo loop

Use `slash_invoke(command="/loops", args=["delete", "<demo-loop-id>"])`
for the recorded, stopped demo loop. Skip when none was created. The real
repository's ongoing loop remains outside the cleanup resource set.

Complete when the demo schedule is removed or its cleanup blocker is recorded.
Update the external resource record after each deletion so cleanup is resumable.

### 15. Display the result

Link the saved local report in a Markdown reply. State whether a scheduled
repair succeeded, failed, or timed out, and list removed or remaining demo
resources. Preserve cleanup failures in the saved record and report them;
do not ask again whether to perform already-authorized cleanup.

Complete when the reply has delivered the result and cleanup status. Return
to the main skill's ongoing-monitoring step when a real repository was chosen.
