---
name: scheduling-github-ci-repairs
description: >-
  Sets up ongoing local monitoring of one repository's open pull requests,
  automatically editing, testing, and pushing fixes for failing GitHub Actions
  checks. Offers a disposable private-repository demonstration when no target
  PR was supplied. Use for recurring PR repair or the local CI onboarding demo.
  A one-off PR repair belongs to repair-github-ci.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-14
  usecases:
    - For configuring ongoing repair of failing pull requests in one repository.
    - For demonstrating a scheduled repair in a disposable private repository.
  requires:
    - GitHub write access to the watched repository and an authenticated coding agent
    - Git installed on the scheduler host; repair checkouts are created automatically
    - For the demo, a GitHub token that can create a private repository and an example PR
  version: "7.3"
script_tools: references/script-tools.md
includes:
  - common/ask_once.md
---

# Onboarding for Scheduled CI fixes

Monitor one repository every **30 seconds** and automatically edit, test,
and push fixes to one failing PR branch per tick. A green PR does not stop
monitoring.

The optional private demo uses the same repair policy and the same cadence.

## Goal

Get the user to a running scheduled loop that repairs a failing PR, and show
one real repair as fast as possible in well under five minutes.

## Plan

Use `update_plan` to create the live plan from the
numbered workflow headings below. Mark Step 8 with `verifies: true` in every `update_plan` call: it is the check that the repair happened, and the report step relies on it to close.

- [ ] Step 1. Check prerequisites: GitHub identity and scopes, then the scheduler.
- [ ] Step 2. Select the repository, or the private demo, with ask_user_choice.
- [ ] Step 3. Select the failing PR, or confirm the authorized demo scope.
- [ ] Step 4. Create the demo repository, failing branch, and PR (demo only).
- [ ] Step 5. Confirm GitHub reports the failure with list_github_actions_workflow_runs.
- [ ] Step 6. Schedule the bounded repair with schedule_ci_repair_loop and record its task id.
- [ ] Step 7. Run the first tick with `/cron run <id>` and read its report.
- [ ] Step 8. Verify the repair with one `pr view` call.
- [ ] Step 9. Save evidence, remove the demo loop and resources, verify with `/cron list`.
- [ ] Step 10. Respond with the outcome report as Markdown.
- [ ] Step 11. After the report is shown, offer the follow-up with `ask_user_choice`.

## Workflow

### Step 1. Check prerequisites

Two calls, one per response:

**confirm authentication and print token**
- `github_cli` `["api", "user", "--include"]` — confirms authentication and
  prints the token's `X-Oauth-Scopes` header.

**check scheduler health**
- `slash_invoke` `{"command": "/cron", "args": ["list"]}` — confirms the
  scheduler answers.

**Complete this step when:**
- GitHub identity, token scopes, and the scheduler are confirmed.

### Step 2. Select the repository


Use the repository already named by the user and skip the rest of this step.
Otherwise, two calls, one per response:
**find what is red right now**
- `scan_github_ci_health()` — every repository of the user's account and
  organizations, default branch and open PRs only. Read `failing_prs`;
**ask once**
- `ask_user_choice` titled "CI Repair Target": "Private disposable demo
  repository" first (recommended), then one option per repository that
  still has at least one failing PR, labelled `owner/repo — N failing PRs`,
  most failures first, at most six. A repository with no failing PR is not
  offered; a loop there would idle. If the scan returns
  `available: false`, offer the demo and the configured repositories as
  before and say the live scan was unavailable.

Choosing the demo authorizes creating and deleting one private repository,
its branch, PR, and loop. Keep the scan result: Step 3 selects the PR from
it without a second GitHub read.

**Complete this step when:**
- When the repository, or the demo scope, is established.

### Step 3. Select the failure scenario

**Existing repository**
- `summarize_github_pr_status(owner, repo, state="open",
  include_checks=true)`
- pick the user's PR, or the first PR with a failing
  check
- do not use forked repository PRs, they will not work.
- If none is failing, the loop still starts in Step 6 and Step 7 is
  skipped.

**Demo repository creation ("Demo")**
The scope was authorized in Step 1; nothing to fetch.

**Complete this step when:**
- PR from existing repository is selected or the demo is authorized to create a PR in a demo repository.

### Step 4. Create the demo failure (Demo only)

Do exactly three calls, in order. Use a simple demo, e.g. calculator.add incorrectly subtracts, with one fast CI test.

**[1] Create repo**
First check if a demo repo already exists:

`github_cli ["repo", "list", "--limit", "100", "--json", "name,url,createdAt,isPrivate", "--jq", "[.[] | select(.name | startswith(\"opensre-ci-repair-demo-\"))]"]`

If one already exists then reuse in place, if it doesn't exist yet then create a new one:

`github_cli ["repo", "create", "opensre-ci-repair-demo-<random>", "--private", "--add-readme", "--description", "Temporary OpenSRE scheduled CI repair demo"]`

No owner prefix, so the authenticated user keeps deletion rights.

**[2] Populate failing demo into repo**
`seed_demo_repository(repo="<owner>/<repo>")`

Record workspace and `head_sha`.` If it fails, inspect stage and saved progress before retrying. Stop on unexpected local or remote changes.

**[3] Create PR from the intentionally broken branch into main.**
Create the broken CI incident that the demo agent is supposed to repair:

`github_cli ["pr", "create", "--base", "main", "--head", "demo/failing-ci", "--title", "Demo: repair failing calculator CI", "--body", "<BODY_COMES_HERE>]`

With the body constant (BODY_COMES_HERE) defined as "Temporary OpenSRE demo: this branch introduces a regression in `calculator.add` that breaks the `Demo calculator CI` workflow (`python -m unittest -v`). A scheduled OpenSRE repair loop is expected to detect the failing check, push a fix commit to this branch without touching `test_calculator.py`, and turn the checks green. Do not merge; the repository is disposable and can be deleted after the demo."

**Complete this step when:**
- The PR URL is returned to the user.

### Step 5. Confirm the failure

`list_github_actions_workflow_runs(owner, repo, branch=<head branch>)`

If the run is still queued or in progress, wait 10 seconds with one `shell_run` `sleep 10` and call it again; do not use any other status tool.

Record the failed run id and head commit. 

**Complete this step when:**
- When GitHub reports a failed run on the PR head.

### Step 6. Schedule the bounded repair

One call for the PR selected in Step 3 or created in Step 4:

`schedule_ci_repair_loop(owner="<owner>", repo="<repo>", pr_number=<n>)`

The tool starts and checks the local background scheduler itself, registers a real 10-second cron task whose tick calls the CI fixer directly, and stops the task on its own once the PR is green or ten minutes have passed. 

It asks for one approval

Record `task_id`, `pr_url`, and `next_run` from the result. `task_id` is the scheduler's task id: `/cron list` and `/cron logs <task_id>` read it. 

If the result says `reused: true`, an earlier run for the same PR is still active; keep its id and deadline and do not schedule again.

**Complete this step when:**
- Task id is recorded.

### Step 7. Run the first tick

`slash_invoke` `{"command": "/cron", "args": ["run", "<id>"]}`. It blocks
until the repair finishes and prints the tick's report; that report is the
detection and repair evidence. Follow with one
`{"command": "/cron", "args": ["logs", "<id>", "--limit", "1"]}` only if the
run output did not include the status.

Skip this step when Step 3 found no failing PR.

Read the work outcome separately from delivery: a delivered report can describe
a blocked or failed repair. 

If delivery failed after work completed, retry with
`/cron run <id> --failed-only`; this resends the retained report.


**Complete this step when:**
- The tick reports `Outcome: succeeded` with a repair commit, or
- 5 retries (below) have been made and its outcome, whatever it is, is recorded.

**Troubleshooting:**
- For no-op, or a refusal, return to the user its reason and record it. 
- Then nudge the coding agent to do another attempt to resolve the issue based on the latest information


### Step 8. Verify the repair

One call: `github_cli ["pr", "view", "<n>", "--json", "headRefOid,commits,statusCheckRollup"]`

The head must be a new commit by the fix, every rollup entry `SUCCESS`, and the test file untouched in the fix commit's file list. 

Do not run the tests locally, do not fetch the same state through a second tool, and do not clone the repository again.

If a check is still running, wait 20 seconds once and repeat the same call.

**Complete this step when:**
- The head commit's checks pass; otherwise try again.

### Step 9. Clean up (demo only)

In this order, no verification calls in between:

1. `write_demo_evidence(repo, pr_number, loop_id, outcome, failed_run_id,
   fix_commit, passing_run_id, blocker)` saves evidence under
   `~/.opensre/demo-results/` and removes the owned temp checkout. Omit
   unavailable IDs for failed or blocked demos. Record the returned evidence
   path and `checkout_removed` status. If saving fails before cleanup, the
   helper retains the checkout. Continue to loop removal after any failure.
2. Always call `slash_invoke` `{"command": "/cron", "args": ["remove", "<id>"]}`,
   including after an evidence-tool failure. The
   demo repository is never deleted; report that the repository remains.
3. `slash_invoke` `{"command": "/cron", "args": ["list"]}` as the single
   verification.

For an existing repository the loop stays; only record its id.

Complete when the loop is gone and remaining resources are documented.

### Step 10. Report

Respond with the report as Markdown, linking the PR inline: PR, failed run id, loop id, fix commit, final check result, cleanup status, evidence path.

Claim success only when detection, scheduled repair, passing checks, and (for the demo) loop removal are all evidenced.

**Complete this step when:**
Complete when the report has been shown to the user as Markdown text.

### Step 11. Offer the follow-up question

After the report is shown, one `ask_user_choice`: 
- "Set up remote continious monitoring
- "Set up local monitoring for another repository"
- "Exit demo"

**Complete this step when:**
- Complete when the menu has been offered.
