---
name: onboarding-cicd-fix
description: >-
  Master onboarding skill: asks which of four CI/CD demos to run, then loads
  and follows the selected child skill. Use on interactive-shell startup,
  for a demo or getting-started request, or for capability questions such as
  "what can you do?". Direct repository analysis, recurring-loop setup, Slack
  setup, and CI-fix requests should load their specialist skill directly.
metadata:
  owner: Tracer Team
  usecases:
    - Interactive-shell startup and /demo
    - Show the available onboarding paths and follow the selected child skill
    - Answer capability and getting-started questions with an interactive demo
  requires:
    - Interactive terminal for the Ask User menu
  type: onboarding
  version: "2.0"
  dependencies:
    - core/agent_harness/prompts/skills/onboarding_cicd_fix/a_local_analysis/SKILL.md
    - core/agent_harness/prompts/skills/onboarding_cicd_fix/b_local_scheduled_loops/SKILL.md
    - core/agent_harness/prompts/skills/onboarding_cicd_fix/c_remote_managed_service/SKILL.md
    - core/agent_harness/prompts/skills/onboarding_cicd_fix/d_remote_slack/SKILL.md
# A router leaves tool scope open so its children and custom requests can run.
tools: []
# The host runs this on entry (startup, /demo, skill_view) before any model step.
pre_execute:
  - tool: ask_user_choice
    args:
      title: Which demo would you like me to run? (Esc to skip)
      note: >-
        Choose a demo using your own repositories or connect your team through
        Slack. The managed-service option is coming soon.
      options:
        - Explore a repo and analyze its CI/CD performance (recommended)
        - Set up an agent that improves CI/CD reliability over time
        - Run CI/CD improvements with a managed service (coming soon)
        - Connect OpenSRE to Slack and hand off DevOps chores for your team
---

# CI/CD onboarding

This master skill owns the onboarding question. Its menu is declared in
`pre_execute` and opens when the skill is entered; if the current message
already answers it, continue directly to the selected child. Never ask the
onboarding question twice for one request.

## Ask User

The menu opens on entry, without you. Do not call `ask_user_choice` yourself
and do not narrate before it; end the turn and wait for the answer. The UI adds
`Or type your own answer...`. If the entry result reports that the menu is
unavailable, show the four `pre_execute` options as a numbered list and wait
for a reply.

## Follow the selected child

The next message carries the question and the user's answer. Call `skill_view`
with the matching name, then follow its returned instructions in the same turn:

- Option A: `cicd-analytics-demo` — [local analysis](a_local_analysis/SKILL.md).
- Option B: `cicd-reliability-agent` — [scheduled loops](b_local_scheduled_loops/SKILL.md).
- Option C: `remote-managed-service` — [managed service](c_remote_managed_service/SKILL.md).
- Option D: `slack-handoff` — [Slack handoff](d_remote_slack/SKILL.md).

Do not perform the child workflow from this summary; load its full skill first.
The managed-service child explains that it is unavailable and ends the flow.
For a custom answer, treat that text as the user's request and act on it using
the appropriate tools or skill. Do not reopen this menu or force a demo choice.
After a child asks its own question, continue that child rather than returning
to this master menu. Escape cancels onboarding; wait for a fresh user request.
