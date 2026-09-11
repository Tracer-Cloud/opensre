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
    - Keep repairing failing pull requests across one repository over time
    - Demonstrate a scheduled repair in a private repository, then clean it up
  requires:
    - GitHub write access to the watched repository and an authenticated coding agent
    - A matching local checkout available to the existing scheduler host
    - For the demo, permission to create and delete a private repository and its workflow
  type: repair
  version: "3.0"
references:
  - common/ask_once.md
---

# Scheduled CI fixes

Monitor one repository every **2 minutes** and automatically edit, test, and
push fixes to its failing PR branches. A green PR does not stop monitoring.
The optional private demo uses the same repair policy every **minute**; its
result is saved before all of its temporary resources are deleted. Preserve
the existing scheduler and background-service behavior.

Read the runtime reference **before** asking the user for anything: it names
the scheduler entrypoint that can run repairs and the cadence it supports, so
every question asked afterwards is one the loop can honor.

## Plan

Use `update_plan` to create or revise the live CI Fix Loop Progress plan
from the nine steps below, in this order. Statuses mean:

- `completed`: the step's completion condition was met, or the step was
  already satisfied by the request, a handoff, or a user declining an
  optional branch. Record the reason in `explanation`.
- `blocked`: the step's work did not happen because a capability,
  permission, or prerequisite is missing. Name the blocker in
  `explanation`. A blocked step stays blocked; do not run unrelated
  commands to earn a `completed` mark for it, and do not remove it.
- `pending` / `in_progress`: work still ahead. Execute steps one at a time
  and update each only when its completion condition is met.

The demo reference expands its one branch into a separate ordered checklist;
return to this plan after that checklist finishes.

- [ ] Step 1. Verify runtime support for scheduled repairs using skill_view.
- [ ] Step 2. Discover candidate repositories with scan_local_git_workspace.
- [ ] Step 3. Select the repository using ask_user_choice.
- [ ] Step 4. Confirm repair authorization using ask_user_choice.
- [ ] Step 5. Offer the optional private demo using ask_user_choice.
- [ ] Step 6. Run the demo workflow through its skill_view reference.
- [ ] Step 7. Create or reuse the ongoing loop with slash_invoke.
- [ ] Step 8. Verify a scheduled execution through the scheduler's run history.
- [ ] Step 9. Display the outcome as Markdown text.

## Workflow

### 1. Verify runtime support

Read [Runtime requirements](references/runtime.md) with
`skill_view(name="scheduling-github-ci-fixes", reference="runtime")` first,
before any question and before creating any resource. It names the
entrypoint for repair loops (`/loops add --prompt`), the supported cadences,
and the repair policy the tick prompt carries. Confirm from the current
turn's tool catalog that `slash_invoke`, `summarize_github_pr_status`, and
`fix_github_pr_ci` are available. Do not list existing loops here; step 7
reads schedules.

If a required tool is missing from the catalog, mark the steps that depend
on it `blocked` with the missing tool named in `explanation`, and continue
with the steps that remain possible. A missing GitHub token is reported as
`opensre integrations setup github`, then stop.

Complete when the reference has been read and the required tools are
confirmed, or each missing capability is recorded as a blocker on the plan.

### 2. Discover repositories

Call `scan_local_git_workspace()` with no arguments. Retain repository names
and absolute checkout paths from its result. An explicitly selected repository
or an analytics handoff satisfies discovery; scan only if its checkout still
needs locating. A scan result alone does not select a repository.

Complete when discovery has returned, including an empty result, or the
repository and checkout are already known from this session.

### 3. Select the repository

Use a repository already named by the user. A supplied PR identifies its
repository; the ongoing loop covers that repository's open PRs, not only the
supplied PR. Reuse the analytics handoff's repository and checkout without
repeating its analysis.

Otherwise call `ask_user_choice` with the title
`Which repository should the agent watch?`. Offer up to five discovered
GitHub repositories with workflows. Include a note that the loop checks every
2 minutes and automatically edits, tests, and pushes repairs to PR branches.
Use only a repository selected by the user; a public example is not a write
target. End the turn after a question and wait for its answer.

When no repository is available, continue to the optional demo with no real
repository selected. Keep real and demo identities separate throughout.
Complete when the real repository and checkout are resolved, or no real
repository is available and the optional demo remains to be offered.

### 4. Confirm repair authorization

Use authorization already supplied in the request or by the repository
picker that described automatic repairs; mark this item satisfied without
asking again. Mark it
satisfied for a demo-only run as well; that branch has its own lifecycle
choice in step 5.

Only when authorization is missing, call `ask_user_choice` with the title
`Enable automatic repairs for <owner/repo>?`. State in the note exactly what
is being authorized: the scheduled loop will edit, test, commit, and push to
failing PR branches without asking again. Offer `Enable automatic repairs`
and `Cancel setup`. End the turn and wait for the answer. Cancellation goes
directly to step 9 with nothing scheduled.

Complete when repair authorization is established or the user cancels.
Individual repair pushes need no additional question. Merging PRs is outside
this workflow.

### 5. Offer the optional demo

A supplied target PR skips this offer unless the user explicitly requests a
demo. Reuse an existing demo choice. Otherwise call `ask_user_choice` with
the title `Should I create a broken pull request to demonstrate the fix?`:

- Yes, create a private demo and delete it after saving the result
- No, continue without the demo

Explain in the question's note that the demo creates a private repository,
runs scheduled repairs every minute for at most 30 minutes, saves a
success or failure report, then deletes its repository, checkout, and loop.
Acceptance covers that lifecycle; cleanup does not require a second menu.
End the turn after asking and wait for the answer.

Complete when the choice has arrived or the offer is skipped. Declining with
no real repository selected goes directly to step 9 with nothing scheduled.

### 6. Run the optional demo

Mark satisfied when declined or when a supplied PR bypassed the offer. Leave
`blocked` without executing when step 1 blocked a tool the demo needs.
Otherwise load [Private demo](references/demo.md) with
`skill_view(name="scheduling-github-ci-fixes", reference="demo")` and follow
its ordered checklist. It owns fixture creation, proof from a scheduled run,
saving evidence, and cleanup on both success and failure.

Complete when the demo checklist has saved its result and accounted for every
created resource. A failed delete remains a cleanup blocker in the final
report; do not claim that resource was removed. Return to step 7 even if the
demo's repair timed out.

### 7. Create or reuse ongoing monitoring

Mark satisfied when no real repository was selected. Leave `blocked` without
executing when step 1 blocked `slash_invoke` or `fix_github_pr_ci`.

This is the one step that reads existing schedules: call
`slash_invoke(command="/loops", args=["all"])` and reuse a loop only when its
name is `CI fix agent · <owner>/<repo>` and its prompt names the same
checkout; leave unrelated schedules unchanged. Otherwise create the loop:

```text
slash_invoke(command="/loops", args=["add", "--name", "CI fix agent · <owner>/<repo>", "--cron", "*/2 * * * *", "--channel", "shell", "--prompt", "<tick prompt>"])
```

The tick prompt is one list element with no line breaks and no token
starting with `--`. It carries the repair policy from the runtime reference
with `<owner>`, `<repo>`, and the absolute `<checkout>` filled in: discover
open PRs with `summarize_github_pr_status`, repair each failing head with
`fix_github_pr_ci(..., workspace="<checkout>")`, keep the fixer's refusals
as reasons, treat running checks as pending, and reply one line per red PR
or `No failing checks on <owner>/<repo>.`. The fixer owns edits, tests,
commits, pushes, and CI verification; the prompt does not add `git` or `gh`
steps around it. Do not load this onboarding skill on each tick.

Keep output in the local shell inbox. State what the existing host requires
to run; configuring, installing, or removing a background service is not a
setup step.

Complete when the `slash_invoke` observation carries the loop's `id`, cron,
and next run, or an existing matching loop was confirmed with `/loops all`.
An error in the observation is relayed in one line and blocks this step.

### 8. Verify a scheduled execution

Mark satisfied for a demo-only run. Leave `blocked` without executing when
the real-repository schedule could not be created because of a blocker.

Read the loop's run history with
`slash_invoke(command="/loops", args=["show", "<id>"])` until an actual
scheduled tick has run, or a named runtime failure prevents it. Bound the
wait to a few reads spaced by the 2-minute cadence. A manual `/loops run` is
an ad-hoc test, not proof that the schedule fired. A tick with no failing PRs
proves monitoring ran; do not claim a repair occurred.

A repair is verified only when checks for the repaired PR's current pushed
commit passed. Pending checks, missing checks, `no_failing_checks`, a push,
and successful report delivery are insufficient. Preserve unresolved or
paused PR outcomes in the report. Keep the ongoing loop enabled after green
checks; future failures remain in scope.

Complete when a scheduled execution is observed or a specific blocker is
recorded. If execution is still pending, report it as pending in step 9.

### 9. Display the outcome

Respond with Markdown text from returned evidence. Preparing a report or
updating the plan does not deliver it; the reply itself carries the outcome.

For a configured loop, show the real repository, loop ID, actual cadence and
next run, verification state, and current host requirements. Give
`/loops messages`, `/loops stop <id>`, and `/loops delete <id>` as management
commands. If the demo ran, link its saved local report and state its repair
outcome and which resources were removed or remain. A demo-only run ends with
its saved report and cleanup status; it leaves no ongoing loop behind.

When a step was blocked, the reply names each blocker once and says what
was and was not created: if no loop exists, say the scheduled repair loop
was **not set up** and **no new repair schedule was created** — do not
describe the scheduler as empty, since other loops may exist. Offer the
supported next request: a one-off interactive repair of a named pull request
or branch (`fixing-github-ci`). Leave blocked steps blocked on the plan and
mark step 9 `completed` in the closing `update_plan` write.

Complete when the reply itself contains the outcome. Conclude after
displaying it.
