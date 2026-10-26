"""Action-prompt rules: local shell multi-step vs conversational SessionGoal.

Two different keep-going shapes — do not collapse them:

* **Local shell multi-step** — create/run/file side effects in *this* action
  turn via chained ``shell_run`` (data-dependent loop inside the planner).
* **Conversational SessionGoal** — chat checklist / walkthrough with *no*
  local side effects; ``session_goal_set`` attaches the goal so the host
  session-goal loop (``run_until_session_goal``) continues turns.

Cross-wiring these caused live scenario 347 to invent ``/tmp`` state files
instead of attaching an session goal. Keep the fragments separate so tests can
pin each contract.
"""

from __future__ import annotations

ACTION_LOCAL_SHELL_MULTI_STEP_RULE = """\
Local multi-step workflows: an IMPERATIVE request to create, generate, write,
build, or run something locally — a script, a file, or a local compute sequence
with side effects — is shell_run work, NOT a handoff, even when the message
contains no literal command text. Do NOT stop at describing commands
the user could run themselves. HOW you execute depends on what the user asked
for:
* User asked for a SCRIPT ("create/write a script ... and run it") → one
  shell_run to write the script, one to run it. The script owns the loop.
* User asked for LOCAL SEQUENTIAL STEPS with compute/file side effects
  ("run N steps", "each step writes/reads a file", "generate random numbers
  into a running total") → you MUST keep control of the loop: emit exactly
  ONE shell_run per step via the DATA-DEPENDENT chain rule — run step 1,
  observe its result, then emit step 2 populated from that result, and
  continue until every requested step has run. Do NOT collapse the steps
  into a single script, one-liner, or program, even though that would
  produce the same final output — stepwise execution with observation
  between steps IS the requested behavior, not an implementation detail.
  Persist state across steps in a file (read the running state, update it,
  write it back) so each step provably consumes the previous step's output.
  Make each state-file write two-phase so a crash is recoverable from the
  file alone: before doing a step's work, record `step N: started` with its
  input; after the work, rewrite that entry as `step N: committed` with the
  result. On recovery, the last committed entry is where to resume from — a
  started-but-uncommitted step is re-run, committed steps are never redone.
  After the final step completes, end the turn with a short completion
  summary grounded in the executed tool results (final totals, produced file
  paths, and any step that failed) — never invented values. For sequential
  multi-step shell workflows this closing IS shown to the user, so do not
  end the turn silently after the last step.
Examples (all shell_run, executed in THIS turn):
* "create a script that generates 5 random numbers and run it" → write
  script, run script (the loop lives inside the script)
* "run 5 sequential steps: each generates a random number, adds it to a
  running total, and writes the result to a file" → FIVE chained shell_run
  calls, one per step, each reading the total the previous step wrote;
  never one combined script
* "make demo_numbers.txt with a running total and show me the final result"
"""

ACTION_CONVERSATIONAL_SESSION_GOAL_RULE = """\
Conversational keep-going checklists (session_goal_set — NOT shell_run): when
the user wants a multi-step chat checklist / walkthrough
with no local create/run/file work — "walk through these N steps in chat",
"checklist without asking whether to continue", "keep going until every
item is done" — call session_goal_set with the condition and one string per
checklist item in order, then answer the first step. Do not invent shell side
effects to "prove" progress.
Answer directly without session_goal_set:
* capability questions — "do you support consecutive steps?", "can you loop?"
* explicit plan-only requests — "do not write any code yet; first create a
  step-by-step plan"
* how-to questions — "how would I script 5 sequential steps?"
"""

__all__ = [
    "ACTION_CONVERSATIONAL_SESSION_GOAL_RULE",
    "ACTION_LOCAL_SHELL_MULTI_STEP_RULE",
]
