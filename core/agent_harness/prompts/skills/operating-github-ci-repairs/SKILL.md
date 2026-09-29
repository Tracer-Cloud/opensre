---
name: operating-github-ci-repairs
description: >-
  On the hosted gateway, execute a delegated bounded GitHub PR repair or private
  demo and inspect an existing repair by task ID. Use for remote execution and
  remote repair status; interactive account setup belongs to delegating-github-ci-repairs.
metadata:
  owner: Jan
  last_changed_by: Jan
  last_changed_at: 2026-09-29
  usecases:
    - For the hosted gateway executing a repair delegated by the interactive shell.
    - For retrieving the retained outcome of an existing remote repair task.
  requires:
    - A hosted gateway with its scheduler running and an authenticated coding agent.
    - The schedule_ci_repair_loop and get_ci_repair_loop tools.
    - Gateway GitHub write access to the selected PR; demo mode also needs private-repository creation.
  version: "1.1"
---

# Operate a remote CI repair

Execute or inspect the selected repair on this gateway. Account setup and
gateway lifecycle belong to the calling shell. Use the instructions already
loaded in this conversation when continuing an approval or status request.

## Plan

Create or revise the live plan with `update_plan`

- [ ] Step 1. Ask for missing target information with ask_user_choice.
- [ ] Step 2. Schedule the selected bounded repair with schedule_ci_repair_loop.
- [ ] Step 3. Inspect its outcome and evidence with get_ci_repair_loop.
- [ ] Step 4. Return the task ID, outcome, and evidence as Markdown.

## Workflow

### Step 1. Ask for missing target information

Read the supplied facts and previous results. A `task_id` selects observation
of that existing repair. Otherwise accept demo mode or a PR URL / owner,
repository, and PR number. Ask only for missing target information; the
gateway relays the question to the shell. A repo or branch alone does not
select a PR, and a known demo choice needs no second target question.

Complete when execution or observation is selected and its target is known.

### Step 2. Schedule the repair

For an existing task ID, continue directly to Step 3. For new work, call one of:

- Demo: `schedule_ci_repair_loop(demo=True)`.
- Selected PR: `schedule_ci_repair_loop(owner="<owner>", repo="<repo>", pr_number=<n>)`.

Let the tool own demo provisioning, scheduler registration, and the bounded
repair. Use its approval mechanism; after an approval answer, resume the
pending invocation with the same arguments. Record `task_id`, `deadline`,
`status`, and any PR URL. If `reused: true`, retain that task and its original
deadline. A refusal is a blocker to report, not a reason to choose another PR.

The tool uses the gateway's scheduler and a 30-second trigger, with at most
three failed attempts and a ten-minute bound. Its `manual_loop` task dispatches
the repair worker directly; it does not require a scheduled skill-file path.
Completed bounded repairs stop their schedules and retain history. Inspect
the selected task's outcome instead of treating paused historical loops as
misconfiguration or re-enabling them.

Complete when a task ID is returned, or record the tool's refusal or setup
failure and report it. Observation never requires another scheduling call.

### Step 3. Inspect the outcome

Call `get_ci_repair_loop(task_id="<id>", wait_seconds=60)` to observe the selected
repair. For a request for current status only, omit the wait. When waiting for
completion, repeat while the task is nonterminal and time remains within its
original deadline and the current turn's budget. If the turn must end first,
return the task ID, pending status, and deadline so the caller can inspect it
later without restarting the work.

Use `terminal`, `status`, and the retained `response_text`. A successful repair
has a verified repair commit and passing checks; a successful demo also has
the observed failing run. GitHub read/write permission flags and historical
storage records cannot replace that evidence. A stopped schedule is expected
after a terminal repair. Report missing evidence rather than inventing it.

Complete when the inspection tool has returned the current outcome and its
available evidence, or its concrete retrieval error. Distinguish pending work
from terminal failure and verified success.

### Step 4. Return the result

Return the `task_id`, target PR if known, status, terminal flag, original
deadline, and the tool's retained report with its CI links and resource status.
For setup failure, return the blocker and any existing task ID. Leave pending
work running. This is a bounded repair, not an always-on monitoring service.

Complete when the caller has either the task's outcome or a precise blocker
and enough identity to continue. The interactive shell owns the final
follow-up menu.
