---
name: scheduling-github-ci-repairs
description: >-
  Sets up ongoing local monitoring of one repository's open pull requests,
  automatically editing, testing, and pushing fixes for failing GitHub Actions
  checks. Offers a disposable private-repository demonstration. Use for recurring PR repair or the local CI onboarding demo.
getting_started: Run continuously on this machine (recommended)
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-10-02
  usecases:
    - For configuring ongoing repair of failing pull requests in one repository.
    - For demonstrating a scheduled repair in a disposable private repository.
  requires:
    - GitHub write access to the watched repository and an authenticated coding agent
    - Git installed on the scheduler host; repair checkouts are created automatically
    - For the demo, a GitHub token that can create a private repository and an example PR
  version: "0.78"
script_tools: references/script-tools.md
---

# Onboarding for Scheduled CI fixes

Monitor one repository every **30 seconds** and automatically edit, test,
and push fixes to one failing PR branch per tick. A green PR does not stop
monitoring.

The private demo uses the same repair policy and the same cadence.

## Goal

Get the user to a running scheduled loop that repairs a failing PR, and show
one real repair as fast as possible in well under five minutes.

## Plan

Use `update_plan` to create the live plan from the workflow headings below. Mark a step `in_progress` or `completed` in the same response as that step's tool call. A response that only calls `update_plan` is not progress.

- [ ] Check prerequisites: GitHub identity and scopes, then the scheduler.
- [ ] Select the repository, or the private demo, with ask_user_choice.
- [ ] Select the failing PR, or confirm the authorized demo scope.
- [ ] Create the demo repository, failing branch, and PR with `seed_ci_repair_demo` (demo only).
- [ ] Schedule the bounded repair with schedule_ci_repair_loop and record its task id.
- [ ] Wait until the repair is terminal with one `get_ci_repair_loop` call and read its report.
- [ ] Verify the repair with one `pr view` call.
- [ ] Save evidence, remove the demo loop, and verify with one `finish_ci_repair_demo` call.
- [ ] Respond with the outcome report as Markdown.
- [ ] After the report is shown, offer the follow-up with `ask_user_choice`.

## Workflow

### Step 1. Check prerequisites

Two calls, one per response:

**confirm authentication and print token:**

- `github_cli` `["api", "user", "--include"]` — confirms authentication and
  prints the token's `X-Oauth-Scopes` header.

**check scheduler health:**

- `slash_invoke` `{"command": "/cron", "args": ["list"]}` — confirms the
  scheduler answers.

**Complete this step when:**

- GitHub identity, token scopes, and the scheduler are confirmed.

### Step 2. Select the repository

Use the repository already named by the user and skip the rest of this step.
An opening answer `Create <owner>/<repo>` or `Create <repo>` names that demo repository. Skip the scan and `ask_user_choice`. A name without an owner uses the login from Step 1. `Don't create a demo repository` keeps the picker below.
Otherwise, two calls, one per response:

**find what is red right now:**

- `scan_github_ci_health()` — every repository of the user's account and
  organizations, default branch and open PRs only. Read `failing_prs`;

**ask once:**

- `ask_user_choice` titled "CI Repair Target": "Private disposable demo
  repository" first (recommended), then one option per repository that
  still has at least one failing PR, labelled `owner/repo — N failing PRs`,
  most failures first, at most six. A repository with no failing PR is not
  offered; a loop there would idle. If the scan returns
  `available: false`, offer the demo and the configured repositories as
  before and say the live scan was unavailable.

Choosing the demo authorizes creating a private repository, its branch, PR, and loop. Keep the scan result: Step 3 selects the PR from it without a second GitHub read.

**Complete this step when:**

- When the repository, or the demo scope, is established.

### Step 3. Select the failure scenario

**Existing repository:**

- `summarize_github_pr_status(owner, repo, state="open", include_checks=true)`
- pick the user's PR, or the first PR with a failing check
- do not use forked repository PRs, they will not work.
- If none is failing, the loop still starts in Step 6 and Step 7 is
  skipped.

**Demo repository creation ("Demo")**
The scope was authorized in Step 1; nothing to fetch.

**Complete this step when:**

- PR from existing repository is selected or the demo is authorized to create a PR in a demo repository.

### Step 4. Create the demo failure (Demo only)

Call `seed_ci_repair_demo(owner="<owner>", repo="<repo>")`. One failed seed is the blocker. Do not call `seed_ci_repair_demo` again in this plan. Report the tool's error text.

The tool treats a 404 from `GET /repos/{owner}/{repo}` as absence and creates the private repository only in that case. It commits a passing `main` (`calculator.py` adding, `test_calculator.py` asserting `add(2, 3) == 5`, and `.github/workflows/test.yml` named `Demo calculator CI` running `python -m unittest -v` on push and pull_request), then one commit on `demo/failing-ci` that changes only `calculator.py` so `add` subtracts, opens that pull request into `main` with a body that says it is a demo not to merge, and returns after the pull-request Actions run has failed. Record `pr_url`, `pr_number`, `head_sha`, and `failed_run_id`. An existing demo repository and pull request are reused. If that repository is not an OpenSRE CI repair demo, the tool leaves it unchanged and seeds `opensre-ci-repair-demo-<4 lowercase letters or digits>` on the same owner. Record `owner`, `repo`, `pr_url`, `pr_number`, `head_sha`, and `failed_run_id` from the result, and use that owner and repo in later steps. Stay in this plan. Do not call `ask_user_choice`, do not end the turn, and do not delete or overwrite the refused repository.

This step uses that one tool. `github_cli`, `list_github_actions_workflow_runs`, an organization repository listing, a code search, and plain `git` are outside this step.

**Complete this step when:**

- `pr_url` and `failed_run_id` are recorded.

### Step 5. Schedule the bounded repair

One call for the PR selected in Step 3 or created in Step 4:

`schedule_ci_repair_loop(owner="<owner>", repo="<repo>", pr_number=<n>)`. For a demo, `<owner>` and `<repo>` are the ones the seed result returned.

The tool starts and checks the local background scheduler itself, registers a real 30-second cron task whose tick calls the CI fixer directly, and stops the task on its own once the PR is green or ten minutes have passed.

It asks for one approval

Record `task_id`, `pr_url`, and `next_run` from the result. `task_id` is the scheduler's task id: `/cron list` and `/cron logs <task_id>` read it.

If the result says `reused: true`, an earlier run for the same PR is still active; keep its id and deadline and do not schedule again.

**Complete this step when:**

- Task id is recorded.

### Step 6. Watch the repair

Do not run `/cron run <id>`. The scheduler picks the task up at `next_run`,
at most 30 seconds away, and owns the repair from there: attempts, CI
verification, and the deadline. On the hosted gateway a slash command is
stopped after 90 seconds, and a stopped `/cron run` takes the repair with it.

Call `get_ci_repair_loop(task_id="<id>", wait_until_terminal=true)` once. The tool waits until `terminal` is true or the repair deadline has passed, and returns that result from this single call. Its `response_text` is the detection and repair evidence.

Skip this step when Step 3 found no failing PR.

Read the work outcome separately from delivery: a delivered report can describe
a blocked or failed repair.

Do not schedule again or ask for another attempt; the task makes up to three
attempts on its own. If the result's deadline has passed and the run is still
not terminal, stop waiting and go to Step 7: the PR shows what happened.

**Complete this step when:**

- The result has `terminal: true` and its outcome is recorded, or
- The deadline has passed and the run is recorded as unfinished.

### Step 7. Verify the repair

One call: `github_cli ["pr", "view", "<n>", "--json", "headRefOid,commits,statusCheckRollup"]`

The head must be a new commit by the fix, every rollup entry `SUCCESS`, and the test file untouched in the fix commit's file list.

Do not run the tests locally, do not fetch the same state through a second tool, and do not clone the repository again.

If a check is still running, wait 20 seconds once and repeat the same call.

**Complete this step when:**

- The head commit's checks pass; otherwise try again.

### Step 8. Clean up (demo only)

One call: `finish_ci_repair_demo(repo="<owner>/<repo>", pr_number=<n>, loop_id="<id>", outcome="<success|failed|blocked>", failed_run_id=<id>, fix_commit="<sha>", passing_run_id=<id>)`.

The tool writes evidence under `~/.opensre/demo-results/` for the approved repository name, removes that scheduled task, and checks that `/cron list` no longer shows it. Omit unavailable IDs for a failed or blocked demo. Record the returned evidence path. The demo repository is never deleted; report that the repository remains.

This step uses that one tool. `write_demo_evidence`, `slash_invoke`, `github_cli`, and a shell heredoc are outside this step.

For an existing repository the loop stays; only record its id.

Complete when the loop is gone and remaining resources are documented.

### Step 9. Report

Respond with the report as Markdown, linking the PR inline: PR, failed run id, loop id, fix commit, final check result, cleanup status, evidence path.

Claim success only when detection, scheduled repair, passing checks, and (for the demo) loop removal are all evidenced.

**Complete this step when:**
Complete when the report has been shown to the user as Markdown text.

### Step 10. Offer the follow-up question

After the report is shown, call `ask_user_choice` with the title `Hand off the next failure?`, `allow_custom` false, and this note:

`The local loop runs on this machine every 30 seconds while it is on. The managed-service option is one repair, then it stops.`

Options:
- Run the next fix in the cloud and close your laptop
- Auto-fix failing PRs on one of your repos from your local laptop
- Not now

Complete when the `ask_user_choice` call for this menu has returned in this turn. The user's answer arrives in the next turn. Each branch except `Not now` is owned by a skill: load it with `skill_view` and follow its plan; do not reimplement its steps here.

- **Run the next fix in the cloud and close your laptop:** call `skill_view(name="delegating-github-ci-repairs")` and follow that skill. That skill runs one repair on the hosted gateway. Do not describe it as a loop that keeps running.
- **Auto-fix failing PRs on one of your repos from your local laptop:** call `skill_view(name="scheduling-github-ci-repairs")` and follow that skill again. Do not reuse the private demo. Select a repository the user already uses, so the loop stays after the report.
- **Not now:** acknowledge in one line and conclude.