---
name: onboarding-github-ci
description: >-
  Routes interactive-shell startup, /demo, and explicit onboarding requests
  through a demo picker to the selected child skill. Answer capability
  questions first and offer /demo. Load the specialist directly when the
  user names a demo or requests repository analysis, recurring CI fixes,
  Slack setup, managed-service availability, or a one-off CI fix.
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-12
  usecases:
    - Interactive-shell startup and /demo
    - Select an onboarding path and begin its child workflow
    - Restart onboarding when the user explicitly requests a fresh demo
  requires:
    - Interactive terminal for guided demo selection
  type: onboarding
  version: "2.1"
  dependencies:
    - core/agent_harness/prompts/skills/onboarding-github-ci/a-analyzing-github-ci-performance/SKILL.md
    - core/agent_harness/prompts/skills/onboarding-github-ci/b-scheduling-github-ci-fixes/SKILL.md
    - core/agent_harness/prompts/skills/onboarding-github-ci/c-connecting-slack/SKILL.md
    - core/agent_harness/prompts/skills/delegating-github-ci-fixes/SKILL.md
# The host opens this menu before the model runs.
pre_execute:
  - tool: ask_user_choice
    args:
      title: Which demo would you like me to run?
      note: >-
        Choose a demo using your own repositories or connect your team through
        Slack.
      options:
        - Explore a repo and analyze its CI/CD performance (recommended)
        - Set up an agent that improves CI/CD reliability over time
        - Connect OpenSRE to Slack and hand off DevOps chores for your team
        - Skip the demo and open the shell
      allow_custom: false
---

# CI/CD onboarding

Resolve the user's demo choice and hand execution to its child skill.
The child owns the work, its prerequisites, authorization, and follow-up.

## Plan

The selected child owns the live plan and its `update_plan` calls. This
router's two steps below track selection and handoff only; leave the live
plan to the child. The host may pause for its entry menu before any model
step can run.

- [ ] Step 1. Resolve the demo selection from the request or the host's ask_user_choice menu.
- [ ] Step 2. Load the selected child's instructions with skill_view.

## Workflow

### 1. Resolve selection

Use an explicit demo choice in the current request or the answer to the
onboarding question. Carry the original request, including any repository,
constraints, and existing approvals, into the handoff. The child determines
its own prerequisites; analysis still follows its scan and repository picker.

When selection is unresolved, read the `pre_execute` result:

- `menu: queued`: end the turn and wait for the answer. The host has opened
  the picker; neither another tool call nor a text copy of its options is needed.
- `menu: suppressed`: continue from an applicable existing answer. A greeting
  is an ordinary conversation turn. For an explicit request to reopen the
  demo menu, call `slash_invoke` with `/demo` and wait for its new selection.
- `menu: unavailable`, a hook error, or no menu result: explain that guided
  selection is unavailable in this session, invite a direct task request,
  and end onboarding. There is no replacement text menu.

An ambiguous CI request needs one focused clarification about the desired
outcome before selecting a child. Use `ask_user_choice` when available and
end the turn; otherwise follow the unavailable-picker behavior above.
Handle unrelated requests as the user's new task.

Skip and Escape end onboarding in the shell without an answer for the model.
An explicit `/demo` starts a fresh run: carry inputs from the new request,
and perform the selected workflow again rather than crediting earlier results
as completed work.

Complete when the user's intended child is unambiguous. Opening a menu is
a pause, not a selection; cancellation and unavailable selection end this
flow without a handoff.

### 2. Load the child

Call `skill_view` with the name matching the selected path:

| Path | Skill |
| --- | --- |
| A. Analyze CI performance | [`analyzing-github-ci-performance`](a-analyzing-github-ci-performance/SKILL.md) |
| B. Set up ongoing CI fixes | [`scheduling-github-ci-fixes`](b-scheduling-github-ci-fixes/SKILL.md) |
| C. Connect Slack | [`connecting-slack`](c-connecting-slack/SKILL.md) |
| Managed service, direct requests only | [`delegating-github-ci-fixes`](../delegating-github-ci-fixes/SKILL.md) |

If loading fails, report that the selected demo could not load, retain the
choice, and stop. Leave handoff incomplete; a new user request can retry it.
The child's description is insufficient to execute its workflow.

Complete when `skill_view` successfully returns the selected child's full
instructions. A hook error inside a successfully loaded child belongs to
that child's workflow.

Immediately follow the child's first unmet step in the same turn, continuing
until its own pause or completion condition. The child starts its live plan
and handles any missing authorization, preserving approvals already given.
Resume that child on its answers; loading it is not a reason to end execution
or return to this menu.
