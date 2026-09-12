---
name: scheduling-github-ci-fixes
description: >-
  Schedule bounded background repair of one GitHub pull request, or demonstrate
  scheduled discovery and repair of a tiny failure in a reusable private repository.
  Use for the local CI onboarding demo or unattended repair of a selected PR.
  A one-off foreground repair belongs to fixing-github-ci.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-12
  usecases:
    - For new users trying an initial scheduled CI repair demonstration.
    - For maintainers scheduling bounded repair of a selected pull request.
  requires:
    - A saved GitHub connection with repository write access and an authenticated coding agent.
    - macOS or Linux with permission to run the local background scheduler service.
    - For initial demo provisioning, permission to create a private repository and its workflow.
  version: "6.1"
includes:
  - common/ask_once.md
---

# Scheduled CI repair

Demonstrate a real scheduled repair with a roughly four-minute target and a
hard ten-minute budget covering setup through the final report. The tool owns
the persistent repository name, fixture, deadline, cancellation, and cleanup.

## Plan

Use `update_plan` to create the live plan from these five workflow steps.
Mark an already-selected scope completed. Send `update_plan` in the same
response as the next tool call, except before `ask_user_choice`, which must
be the only call in its response. Keep report delivery and follow-up as
separate steps.

- [ ] Step 1. Select the reusable demo or a specific PR with ask_user_choice.
- [ ] Step 2. Register or reuse the bounded run with schedule_ci_repair_loop.
- [ ] Step 3. Observe scheduled execution with get_ci_repair_loop.
- [ ] Step 4. Deliver the tool's structured outcome report as Markdown.
- [ ] Step 5. Offer the next workflow with ask_user_choice.

## Workflow

### Step 1. Select the scope

Use an explicit demo request or PR selection already supplied by the user.
Otherwise ask once with `ask_user_choice`: "Reusable private demo repository"
(recommended), or "Repair a selected PR". Collect the PR owner, repository,
and number only for the second choice. An explicitly requested organization
is the demo owner override; otherwise the tool uses the authenticated user.

Complete when the user selected the demo or a specific PR.

### Step 2. Schedule the run

Call `schedule_ci_repair_loop(demo=true)` for the demo, adding `owner` only
for an explicit owner override. For an existing PR call
`schedule_ci_repair_loop(owner="<owner>", repo="<repo>", pr_number=<number>)`.
This authorizes the background service and the selected repair scope. The
returned task id and deadline are authoritative. If `reused` is true, continue
observing that run; its original deadline remains unchanged.

Show the task id, next scheduled time, and `/loops show <task_id>` retrieval
command. Setup failures are terminal outcomes to report in Step 4. Repository
collisions stop setup; explain the tool's result without adopting another repo.

Complete when a task id or a terminal setup result is returned.

### Step 3. Observe the scheduled repair

Call `get_ci_repair_loop(task_id="<id>", wait_seconds=60)` until `terminal`
is true. The real cron trigger starts the worker; observation does not start
or repeat repair. The worker may perform multiple repair cycles within its
original ten-minute budget. It preserves the test and late-check protection.

The run continues after terminal closure. When the user returns, retrieve its
saved report through `/loops show <task_id>`. A stopped or expired run stays
stopped; another explicit request may start a fresh run in the same repository.

Complete when the run is terminal, or the user chooses to leave it running.

### Step 4. Deliver the result

Display `response_text` as Markdown bullets, preserving its links and factual
outcome. It covers elapsed time, attempts, PR, failing and passing CI evidence,
repair commit, stopped scheduling, retained artifacts, and next steps.
Success cleans up the demo PR and branch; failure retains them for inspection.
The repository and disabled loop history remain available across runs.

Complete when the actual report has been shown, or the retrieval command has
been provided to a user leaving the run in the background.

### Step 5. Offer the follow-up

After showing the report, use `ask_user_choice`: "Repair a real PR" / "No thanks".
A new repair is a separate request with its own scope and deadline.

Complete when the menu has been offered.
