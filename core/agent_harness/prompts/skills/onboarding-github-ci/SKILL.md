---
name: onboarding-github-ci
description: >-
  Master onboarding skill: asks which of four CI/CD demos to run, then loads
  and follows the selected child skill. Use on interactive-shell startup,
  for a demo or getting-started request, or for capability questions such as
  "what can you do?". Direct repository analysis, recurring-loop setup, Slack
  setup, and CI-fix requests should load their specialist skill directly.
metadata:
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-09-11
  usecases:
    - Interactive-shell startup and /demo
    - When users want to see an onboarding flow
    - Show the available onboarding paths and follow the selected child skill
    - Answer capability and getting-started questions with an interactive demo
  requires:
    - Interactive terminal for the Ask User menu
  type: onboarding
  version: "2.0"
  dependencies:
    - core/agent_harness/prompts/skills/onboarding-github-ci/a-analyzing-github-ci-performance/SKILL.md
    - core/agent_harness/prompts/skills/onboarding-github-ci/b-scheduling-github-ci-fixes/SKILL.md
    - core/agent_harness/prompts/skills/onboarding-github-ci/c-delegating-github-ci-fixes/SKILL.md
    - core/agent_harness/prompts/skills/onboarding-github-ci/d-connecting-slack/SKILL.md
# The host runs this on entry (startup, /demo, skill_view) before any model step.
pre_execute:
  - tool: ask_user_choice
    args:
      title: Which demo would you like me to run?
      note: >-
        Choose a demo using your own repositories or connect your team through
        Slack. The managed-service option is coming soon.
      options:
        - Explore a repo and analyze its CI/CD performance (recommended)
        - Set up an agent that improves CI/CD reliability over time
        - Run CI/CD improvements with a managed service (coming soon)
        - Connect OpenSRE to Slack and hand off DevOps chores for your team
        - Skip the demo and open the shell
      allow_custom: false
---

# CI/CD onboarding

This master skill owns the onboarding question. Its menu is declared in
`pre_execute`; the entry result reports whether it opened. If the current message
already answers it, continue directly to the selected child. Never ask the
onboarding question twice for one request.

## Ask User

Read the `pre_execute` results before deciding what to do:

- `menu: queued`: end the turn and wait for the selection. The host owns the
  menu; do not call `ask_user_choice` again or repeat its options as text.
- `menu: suppressed`: no new menu opened. Continue the current request using
  the existing answer. If the user explicitly requests the demo menu again,
  call `slash_invoke` with `/demo`; a greeting does not request reopening.
- `menu: unavailable`: show the `pre_execute` options as a numbered list and
  wait for a reply.
- A hook error without a menu status: explain that the picker could not open
  and show the options as a numbered list.

Only a queued menu justifies telling the user to select from an open picker.
The menu has no free-text row. Its last option opens the plain shell; the
shell handles Skip and Escape without sending an answer to the model.

## Follow the selected child

The next message carries the question and the user's answer. Call `skill_view`
with the matching name, then follow its returned instructions in the same turn:

- Option A: `analyzing-github-ci-performance` —
  [analyze CI performance](a-analyzing-github-ci-performance/SKILL.md).
- Option B: `scheduling-github-ci-fixes` —
  [schedule the CI fix loop](b-scheduling-github-ci-fixes/SKILL.md).
- Option C: `delegating-github-ci-fixes` —
  [delegate to the managed service](c-delegating-github-ci-fixes/SKILL.md).
- Option D: `connecting-slack` — [connect Slack](d-connecting-slack/SKILL.md).

Do not perform the child workflow from this summary; load its full skill first.
The managed-service child explains that it is unavailable and ends the flow.
For a custom answer, treat that text as the user's request and act on it using
the appropriate tools or skill. Do not reopen this menu or force a demo choice.
After a child asks its own question, continue that child rather than returning
to this master menu. Escape cancels onboarding; wait for a fresh user request.
