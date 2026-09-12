---
name: scheduling-github-ci-fixes
description: >-
  Sets up ongoing local monitoring of one repository's open pull requests,
  automatically editing, testing, and pushing fixes for failing GitHub Actions
  checks. Offers a disposable private-repository demonstration when no target
  PR was supplied. Use for recurring PR repair or the local CI onboarding demo.
  A one-off PR repair belongs to fixing-github-ci.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-12
  usecases:
    - For maintainers configuring ongoing repair of failing pull requests in one repository.
    - For users demonstrating a scheduled repair in a disposable private repository.
  requires:
    - GitHub write access to the watched repository and an authenticated coding agent
    - A matching local checkout available to the existing scheduler host
    - For the demo, a GitHub token that can create and delete a private repository
  version: "5.1"
includes:
  - common/ask_once.md
---

# Onboarding for Scheduled CI fixes

Monitor one repository on a schedule and automatically edit, test, and
push fixes to one failing PR branch per tick. A green PR does not stop
monitoring.

The optional private demo uses the same repair policy every **minute**; its
result is saved before its temporary resources are deleted.

## Goal

Get the user to a running scheduled loop that repairs a failing PR, and show
one real repair as fast as possible in well under five minutes.

## Runtime facts

- The loop is a scheduler task: `slash_invoke` with `command: "/cron"`. Cron
  granularity is one minute; there is no 20-second polling.
- Scheduled ticks run headless with the full tool catalog. The tick prompt
  must name the tool call directly; `fix_github_pr_ci` itself reports when a
  PR has no failing checks, so the tick needs no separate status read.
- `fix_github_pr_ci` clones, repairs, pushes, and waits for the new checks
  before it returns. A tick therefore takes a few minutes; `/cron run <id>`
  blocks for that long and returns the tick's report. Do not poll while it
  runs.
- `github_cli` passes `repo` as `-R` only to commands that accept it. `gh repo
  …` takes the repository positionally: `["repo", "create", "<name>", …]`.
- `update_plan` never travels alone: batch it with the next real tool call.
  Read-only verification calls that do not depend on each other go in one
  batch too.

## Plan

After reading this skill, use `update_plan` to create the live plan from the
eleven numbered workflow headings below. Mark steps the request already
satisfies `completed` (a named repository skips Step 1; an existing failing
PR skips Steps 4 and 8) and move each step to `completed` when its
completion condition is met.

- [ ] Step 1. Select the repository, or the private demo, with ask_user_choice.
- [ ] Step 2. Check prerequisites in one batch: GitHub identity and scopes, scheduler.
- [ ] Step 3. Select the failing PR, or confirm the authorized demo scope.
- [ ] Step 4. Create the demo repository, failing branch, and PR (demo only).
- [ ] Step 5. Confirm GitHub reports the failure with list_github_actions_workflow_runs.
- [ ] Step 6. Start the loop with the `/cron add` call and record its task id.
- [ ] Step 7. Run the first tick with `/cron run <id>` and read its report.
- [ ] Step 8. Verify the repair with one `pr view` call.
- [ ] Step 9. Save evidence, remove the demo loop and resources, verify with `/cron list`.
- [ ] Step 10. Respond with the outcome report as Markdown.
- [ ] Step 11. After the report is shown, offer the follow-up with ask_user_choice.

## Workflow

### Step 1. Select the repository

Use the repository already named by the user. Otherwise ask once with
`ask_user_choice`: "Private disposable demo repository" first (recommended),
then the repositories the user has configured. Choosing the demo authorizes
creating and deleting one private repository, its branch, PR, and loop.

Complete when the repository, or the demo scope, is established.

### Step 2. Check prerequisites

One batch of two calls:

- `github_cli` `["api", "user", "--include"]` — confirms authentication and
  prints the token's `X-Oauth-Scopes` header.
- `slash_invoke` `{"command": "/cron", "args": ["list"]}` — confirms the
  scheduler answers.

Complete when GitHub identity, token scopes, and the scheduler are
confirmed.

### Step 3. Select the failure scenario

- Existing repository: `summarize_github_pr_status(owner, repo, state="open",
  include_checks=true)`; pick the user's PR, or the first PR with a failing
  check. If none is failing, the loop still starts in Step 6 and Step 7 is
  skipped.
- Demo: the scope was authorized in Step 1; nothing to fetch.

Complete when a PR is selected or the demo is authorized.

### Step 4. Create the demo failure (demo only)

Exactly three calls, in order. Keep the example relatable — a `calculator.add`
that subtracts — with one test that runs in seconds.

1. `github_cli` `["repo", "create", "opensre-ci-repair-demo-<random>",
   "--private", "--add-readme", "--description", "Temporary OpenSRE
   scheduled CI repair demo"]`. No owner prefix: creating under the
   authenticated user keeps admin rights for deletion.
2. One `shell_run` script that clones into a temp directory, writes
   `.github/workflows/test.yml` (checkout, `actions/setup-python@v5`,
   `python -m unittest -v` on `push` and `pull_request`), `calculator.py`,
   `test_calculator.py`, and `AGENTS.md` ("Run `python -m unittest -v`"),
   commits and pushes `main`, then creates `demo/failing-ci` with the
   subtraction bug, commits, and pushes it.
3. `github_cli` `["pr", "create", "--base", "main", "--head",
   "demo/failing-ci", "--title", "Demo: repair failing calculator CI",
   "--body", "…"]` with `repo` set to the new repository.

Complete when the PR URL is known.

### Step 5. Confirm the failure

`list_github_actions_workflow_runs(owner, repo, branch=<head branch>)`. If
the run is still queued or in progress, wait 20 seconds with one
`shell_run` `sleep 20` and call it again; do not use any other status tool.
Record the failed run id and head commit.

Complete when GitHub reports a failed run on the PR head.

### Step 6. Start the loop

One `slash_invoke` call. Record the `Task <id> created.` id from its output.

```json
{"command": "/cron", "args": ["add",
  "--name", "CI repair: <owner>/<repo>",
  "--kind", "manual_loop",
  "--cron", "*/2 * * * *",
  "--provider", "interactive_shell",
  "--mode", "agent",
  "--prompt", "<tick prompt>"]}
```

Demo loop: same call with `"--cron", "* * * * *"` and the name
`CI repair demo: <owner>/<repo>#<n>`.

Tick prompt for one PR (demo, or a user-selected PR):

> Call `fix_github_pr_ci(owner="<owner>", repo="<repo>", pr_number=<n>)`
> exactly once. If it reports no failing checks, reply `green` and stop. Do
> not touch any other repository or pull request. Reply with the failed run
> id, the fix commit, and the final check state.

Tick prompt for a whole repository:

> Call `summarize_github_pr_status(owner="<owner>", repo="<repo>",
> state="open", include_checks=true)`. Take the first PR in the returned list
> with a failing check and call `fix_github_pr_ci(owner="<owner>",
> repo="<repo>", pr_number=<that number>)` exactly once; a refusal consumes
> this tick's attempt. If no PR is failing, reply `green` and stop. Reply
> with the PR link, the fix commit, and the final check state.

Complete when the output confirms `Mode: agent` and the task id is recorded.

### Step 7. Run the first tick

`slash_invoke` `{"command": "/cron", "args": ["run", "<id>"]}`. It blocks
until the repair finishes and prints the tick's report; that report is the
detection and repair evidence. Follow with one
`{"command": "/cron", "args": ["logs", "<id>", "--limit", "1"]}` only if the
run output did not include the status.

Skip this step when Step 3 found no failing PR.

Complete when the tick reports a fix commit, or a refusal with its reason.

### Step 8. Verify the repair

One call: `github_cli` `["pr", "view", "<n>", "--json",
"headRefOid,commits,statusCheckRollup"]`. The head must be a new commit by
the fix, every rollup entry `SUCCESS`, and the test file untouched in the fix
commit's file list. Do not run the tests locally, do not fetch the same
state through a second tool, and do not clone the repository again.

If a check is still running, wait 20 seconds once and repeat the same call.

Complete when the head commit's checks pass; otherwise record the blocker.

### Step 9. Clean up (demo only)

In this order, no verification calls in between:

1. One `shell_run` that writes the evidence file
   `~/.opensre/demo-results/ci-repair-demo-<date>-<random>.md` (repository,
   PR link, failed run id, loop id, fix commit, passing run id) and removes
   the temp checkout.
2. `slash_invoke` `{"command": "/cron", "args": ["remove", "<id>"]}`.
3. `github_cli` `["repo", "delete", "<login>/<name>", "--yes"]`. If GitHub
   refuses, delete the branch instead with
   `["api", "-X", "DELETE", "repos/<login>/<name>/git/refs/heads/demo/failing-ci"]`
   and report that the repository remains.
4. `slash_invoke` `{"command": "/cron", "args": ["list"]}` as the single
   verification.

For an existing repository the loop stays; only record its id.

Complete when the loop is gone and remaining resources are documented.

### Step 10. Report

Respond with the report as Markdown, linking the PR inline: PR, failed
run id, loop id, fix commit, final check result, cleanup status, evidence
path.

Claim success only when detection, scheduled repair, passing checks,
and (for the demo) loop removal are all evidenced.

Complete when the report has been shown to the user as Markdown text.

### Step 11. Offer the follow-up question

After the report is shown, one `ask_user_choice`: "Set up monitoring for a
real repository" / "No thanks". Remote continuous monitoring is a separate
task with its own scope; it is not a condition of completion.

Complete when the menu has been offered.
