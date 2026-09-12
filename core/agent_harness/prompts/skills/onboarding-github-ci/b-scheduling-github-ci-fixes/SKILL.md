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
  version: "4.0"
references:
  - common/ask_once.md
---

# Scheduled CI fixes

Monitor one repository every **2 minutes** and automatically edit, test, and
push fixes to one failing PR branch per tick. A green PR does not stop monitoring.
The optional private demo uses the same repair policy every **minute**; its
result is saved before its temporary resources are deleted.

## Plan

Track these steps with `update_plan`. Mark a step `completed` when its
condition is met or the request already satisfied it, `blocked` (with the
named blocker) when a capability is missing — never run unrelated commands to
earn a mark. The demo reference owns its own checklist.

- [ ] Step 1. Pick the repository.
- [ ] Step 2. Offer the optional private demo.
- [ ] Step 3. Run the demo through its reference.
- [ ] Step 4. Create or reuse the ongoing loop.
- [ ] Step 5. Verify a scheduled tick.
- [ ] Step 6. Reply with the outcome.

## Runtime

Repair loops are agent-mode prompt loops on the existing scheduler:

```text
slash_invoke(command="/loops", args=["add", "--name", "CI fix agent · <owner>/<repo>", "--cron", "*/2 * * * *", "--mode", "agent", "--channel", "shell", "--prompt", "<tick prompt>"])
```

- Cron is five-field, minute granularity: `*/2 * * * *` for the real loop,
  `* * * * *` for the demo (fastest supported).
- `--mode agent` is required; without it the tick is a read-only report turn
  and cannot repair anything.
- The tick prompt is one list element: no line breaks, no token starting with
  `--`, self-contained (each tick starts a fresh session).
- `/loops show <id>` run history is the only proof a scheduled tick ran; a
  manual `/loops run` is not. Deleting a loop deletes its history, so stop a
  loop and save evidence before deleting it.
- `fix_github_pr_ci` owns each repair — edits, tests, commit, push, and check
  verification — within its own bounded execution. Never wrap it in `git` or
  `gh` steps.
- Each tick is stateless: the first failing PR may be retried on later ticks,
  including after a refusal; rotation across PRs is not guaranteed.

Tick prompt template (fill `<owner>`, `<repo>`, absolute `<checkout>`):

```text
For <owner>/<repo>: call summarize_github_pr_status(owner="<owner>", repo="<repo>", state="open", include_checks=true). Select the first PR in the returned list with failing checks and call fix_github_pr_ci(owner="<owner>", repo="<repo>", pr_number=<number>, workspace="<checkout>") exactly once. Return its response_text exactly and stop, including on refusal; a refusal consumes this tick's attempt. Treat running checks as pending. If no PR has failing checks, reply exactly: No failing checks on <owner>/<repo>. Do not run git or gh around the fixer or load skill_view.
```

## Working style

- No prose between tool calls; call the next tool directly.
- `update_plan` only when a step's status actually changes.
- Question notes are at most two sentences.
- Never echo loop configuration or the tick prompt back to the user.
- Poll `/loops show <id>` at most once per cadence; stop at the first
  decisive run.
- Link every pull request inline as `[#N](https://github.com/<owner>/<repo>/pull/N)`.
- The final reply is roughly ten lines; evidence lives in tool results, not
  restated tables.

## Workflow

### 1. Pick the repository

A repository or PR named by the user, or an analytics handoff, selects the
repository and its checkout directly. Otherwise call
`scan_local_git_workspace()` and ask with `ask_user_choice`, title
`Which repository should the agent watch?`, offering up to five discovered
repositories with workflows. The note states the loop checks every 2 minutes
and automatically edits, tests, and pushes repairs to failing PR branches —
selecting a repository is the repair authorization; do not ask again. A
missing GitHub token is reported as `opensre integrations setup github`, then
stop. With no repository available, continue to the demo offer with none
selected.

### 2. Offer the demo

A supplied target PR skips this offer. Otherwise ask with `ask_user_choice`,
title `Should I create a broken pull request to demonstrate the fix?`, options
`Yes, create a private demo and delete it after saving the result` and
`No, continue without the demo`. The note says the demo creates a private
repository, repairs it on an every-minute loop within 30 minutes, saves a
report, then deletes its repository, checkout, and loop; acceptance covers
that lifecycle. Declining with no real repository selected goes to step 6.

### 3. Run the demo

When accepted, load the checklist with
`skill_view(name="scheduling-github-ci-fixes", reference="demo")` and follow
it; it owns fixture creation, scheduled-repair proof, evidence, and cleanup.
Return here afterwards even if the demo failed or timed out.

### 4. Create or reuse the loop

Skip when no real repository was selected. Call
`slash_invoke(command="/loops", args=["all"])` once; reuse a loop only when
its name is `CI fix agent · <owner>/<repo>`, its prompt names the same
checkout, and `/loops show <id>` confirms `Mode: agent` with the 2-minute
cadence. For a matching loop with the wrong mode or cadence, stop it, wait for
any active run to finish, save its history, and delete it before creating the
replacement. Create with the Runtime entrypoint and tick prompt above. Keep output on the shell channel; installing or removing a background
service is not a setup step. Complete when the observation carries the loop
id, cron, and next run.

### 5. Verify a scheduled tick

Skip for a demo-only run. Read
`slash_invoke(command="/loops", args=["show", "<id>"])` at most twice, spaced
by the 2-minute cadence, until a scheduled tick appears in run history or a
named blocker prevents it. A tick reporting no failing PRs proves monitoring;
a repair is verified only when the repaired PR's current head has green
checks. Still pending after two reads is reported as pending, not failed.

### 6. Reply with the outcome

The reply itself carries the outcome — about ten lines: repository, loop id,
cadence and next run, the verified tick result with inline PR links
(`[#N](https://github.com/<owner>/<repo>/pull/N)`), and one management line:
`/loops messages · /loops stop <id> · /loops delete <id>`. If the demo ran,
add one line with its outcome, PR link, and saved report path, plus any
resource that could not be deleted. Name each blocker once; if no loop was
created, say the scheduled repair loop was **not set up** and offer a one-off
repair via `fixing-github-ci`. Leave blocked steps blocked and mark step 6
completed in the closing `update_plan` write.
