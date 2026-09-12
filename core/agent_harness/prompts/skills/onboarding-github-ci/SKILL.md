---
name: onboarding-github-ci
description: >-
  Present the available onboarding workflows and follow the selected skill. Use at interactive startup,
  for /demo, or when a user asks about OpenSRE capabilities. Direct workflow requests should load the
  matching specialist.
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-12
  usecases:
  - For new users selecting an available onboarding workflow at startup or through /demo.
  - For users exploring OpenSRE capabilities before choosing a specific workflow.
  requires:
  - An interactive terminal for the entry picker, or a conversational surface for its text fallback.
  - At least one available onboarding child skill.
  version: '2.1'
pre_execute:
- tool: ask_user_choice
  args:
    title: Which demo would you like me to run?
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
- `menu: unavailable`: show Current demo choices as a numbered list and
  wait for a reply.
- A hook error without a menu status: explain that the picker could not open
  and show Current demo choices as a numbered list.

Only a queued menu justifies telling the user to select from an open picker.
The menu has no free-text row. Its last option opens the plain shell; the
shell handles Skip and Escape without sending an answer to the model.

## Follow the selected child

The next message carries the question and the user's answer. Call `skill_view`
with the matching name, then follow its returned instructions in the same turn:

Use the generated Current demo choices below to match the answer to its skill.
Load that skill before performing its workflow.

For a custom answer, treat that text as the user's request and act on it using
the appropriate tools or skill. Do not reopen this menu or force a demo choice.
After a child asks its own question, continue that child rather than returning
to this master menu. Escape cancels onboarding; wait for a fresh user request.

An explicit `/demo` starts a fresh run: carry inputs from the new request,
and perform the selected workflow again rather than crediting earlier results
as completed work.
