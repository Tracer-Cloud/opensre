# Runtime requirements

Read this reference first, before asking for repair authorization, offering
the demo, or creating any resource. It names the one scheduler entrypoint
that runs repairs on this runtime and the limits the loop must live within.

## Supported entrypoint

Create repair loops as **prompt loops** through the existing shell command:

```text
slash_invoke(command="/loops", args=["add", "--name", "<loop name>", "--cron", "<five-field cron>", "--channel", "shell", "--prompt", "<tick prompt>"])
```

Facts that decide what the loop can do:

| Fact | Effect on setup |
| --- | --- |
| A prompt loop's tick is one headless agent turn with the full tool catalog, so `summarize_github_pr_status` and `fix_github_pr_ci` are available to it. | Repairs are possible only through `--prompt`. |
| A skill-pinned loop (`opensre cron add --skill …`) runs its ticks restricted to read-only tools. | Never schedule repairs as a skill loop. |
| Cron is five-field with minute granularity; `*/2 * * * *` is the 2-minute cadence and `* * * * *` is the fastest supported. | The real loop runs every 2 minutes. The demo runs every minute; no sub-minute interval exists and shell sleep loops are not a substitute. |
| The scheduler stores the prompt, cron, and channels — no per-PR state. Each tick starts from a fresh session with only the saved prompt. | The tick prompt must be self-contained: repository, checkout path, and the repair policy below. |
| `fix_github_pr_ci` owns each repair: edits, tests, commit, push, and waiting for the new checks, within its own bounded execution. | Do not wrap it in `git` or `gh` commands and do not add a second deadline mechanism. |
| `/loops show <id>` returns the loop's run history; `/loops messages` is the shell inbox; `/loops next <id>` shows the next fire time. Deleting a loop deletes its history. | Proof of a scheduled tick comes from run history, not from a manual `/loops run`. Save evidence before deleting a demo loop. |
| Stopping a loop disables future ticks; a tick already running finishes on its own. | Before deleting demo resources, confirm through `/loops show <id>` that no run is still in progress. |

Tool availability is confirmed from the tool catalog of the current turn; a
listing of existing loops does not prove or disprove any of the facts above.

## Repair policy carried in the tick prompt

Write the policy into the `--prompt` value as plain sentences without line
breaks. The prompt value is read up to the next `--` flag, so it must not
contain a token starting with `--`.

- Call `summarize_github_pr_status(owner="<owner>", repo="<repo>", state="open")`
  and keep the pull requests whose `check_status` is `failed`.
- For each of them, one at a time, call
  `fix_github_pr_ci(owner="<owner>", repo="<repo>", pr_number=<number>, workspace="<checkout>")`
  and take its `response_text` as the outcome. Keep the fixer's refusals
  (closed PRs, fork PRs, no failing checks) as reasons; do not retry them in
  the same tick or change repository scope.
- A pull request whose checks are still running is pending, not failed;
  leave it for the next tick.
- Reply with one line per pull request that had a failing check: number,
  title, the checks that failed, and the repair commit or the reason it was
  left alone. Write `No failing checks on <owner>/<repo>.` when none were
  red. Every figure comes from a tool result.
- The loop reports to the shell inbox only. Setup does not add Slack or
  Telegram recipients, install a service, or change scheduler lifecycle.

## Repository and demo prerequisites

Verify the selected repository and absolute checkout match, write access is
usable, and a coding agent is installed and authenticated. Preserve the
user's working changes. Use the fixer's existing workspace contract; it owns
branch checkout and repairs. If authorization was already supplied during
setup, retain it for later pushes rather than asking on every attempt.

For a demo, also verify private repository creation, workflow-file pushes,
Actions execution, and deletion are authorized by the actual credentials.
OpenSRE's default OAuth scopes do not include `workflow` or `delete_repo`;
write access alone does not prove these operations will work. Resolve missing
access before creating disposable resources. Use the existing GitHub setup
flow for missing credentials; do not change authentication implicitly.

Persist a resource record outside the disposable checkout containing the
returned repository ID and full name, PR URL, absolute checkout path, demo
loop ID, baseline/failing/repaired heads, and report path. Add each identity
as soon as its creation succeeds. This record, not a guessed name or path,
defines cleanup ownership and supports cleanup after partial setup or restart.
