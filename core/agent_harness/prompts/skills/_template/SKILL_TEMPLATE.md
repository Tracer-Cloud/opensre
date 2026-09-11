<!--
SKILL TEMPLATE — not a real skill. The loader (prompts/skills_loader.py)
ignores this file because it is not named SKILL.md / _template.md.

To create a new skill:
1. Copy this folder to skills/<snake_case_name>/ and rename this file SKILL.md.
2. Replace every <placeholder> and delete the HTML comments — the body is fed
   verbatim to the action agent via skill_view(name).
3. Keep the frontmatter `name` kebab-case (must match ^[a-z0-9]+(-[a-z0-9]+)*$,
   otherwise the loader falls back to the folder name).
4. The `description` is the ONLY text in the always-on SKILLS INDEX — make it
   self-sufficient: what the skill does, which tool(s) it drives, and append
   "Multi-step; load before acting." for data-dependent chains.
5. Add `recurring: <human schedule>` (e.g. "weekdays 09:00") only when the
   skill ends with a propose_scheduled_delivery offer using kind recurring_skill.
   Fill the `metadata` block: `owner` (the person creating the skill — a
   name, never a team label; never changed later), `last_changed_by` and
   `last_changed_at` (the person making the current edit and the ISO date
   `YYYY-MM-DD` — update both on every change), `usecases`
   (what a user asks for), `requires` (accounts, tokens, local state), `type`
   (onboarding, analytics, report, repair, audit), `version`, and
   `prerequisite_for` when another skill must run first. The loader ignores
   it; people and the docs read it.
6. Optional report template: a sibling file named <folder>_report.md is
   appended automatically to the body that skill_view returns.
   Optional `references:` lists sibling markdown files (for example
   `common/progress.md`) resolved from the skill folder, its parent
   package, or the skills tree. Each existing file is appended after
   the body; paths that leave the tree are ignored.
7. Optional `getting_started:` (verbatim first-visit demo label) plus
   `demo_order:` (1-based menu row, A=1) attach this skill to `/demo`.
   Optional `pre_execute:` lists static tool calls (`- tool: ask_user_choice`
   + `args:` shaped like the tool's input) the host runs when the skill is
   entered, before any model step; only `ask_user_choice` is allowed.
   Optional `after_tool:` is the same call after a named tool succeeds
   (`after:`, plus `options_from:` / `options_extra:` when the labels come
   from that tool's result). The host opens it; do not also call the menu.
8. Section order below is the house style (see fixing-github-ci for a
   single-tool skill, onboarding-github-ci for a multi-step one). Keep the
   whole body tight — it is loaded into the planner's context on demand.
-->
---
name: <verb-ing>-<object>  # gerund first, kebab-case; see AGENTS.md "Naming conventions"
description: >-
  <One or two lines for the compact index: what the skill does and the main
  tool(s) it uses. Add "Multi-step; load before acting." if data-dependent.>
metadata:
  owner: <person who created the skill>
  last_changed_by: <person making this edit>
  last_changed_at: <YYYY-MM-DD of this edit>
  usecases:
    - <What a user asks for that this skill answers>
  requires:
    - <Account, token, or local state the skill needs>
  type: <onboarding | analytics | report | repair | audit>
  version: "1.0"
---
══════════════════════════════════════════════════════════
<UPPERCASE SKILL TITLE> SKILL — interactive-shell action agent:
══════════════════════════════════════════════════════════

WHEN TO USE:
- <User asks that should trigger this skill.>
- <Quote concrete trigger phrases: "fix CI on this PR", "audit owner/repo", …>

USE THIS TOOL:
- `<primary_tool_name>`

DO NOT USE THIS SKILL FOR:
- <Adjacent request that belongs to another skill/tool, and which one to use
  instead, e.g. "Ordinary PR reads … Use `github_cli`.">

HARD RULES:
- <Exact call shapes: `<tool>(arg="<value>")` for each input variant
  (URL form, owner/repo#N form, bare/default form).>
- <Defaulting rule when the user names nothing, e.g. "omit owner/repo and let
  the tool use the current checkout's GitHub origin".>
- <Tools the agent must NOT substitute (raw shell_run / gh) and why the owned
  tool covers that workflow end-to-end.>
- <Output contract: what the final reply looks like, e.g. "output
  response_text exactly and stop", "reply in one short line from `error`",
  "final reply is the filled REPORT TEMPLATE".>

<!-- For multi-step, data-dependent skills replace or follow HARD RULES with
     numbered steps. State explicitly which calls are independent (same turn)
     and which must WAIT for prior results (next turn), and forbid fabricating
     data the reads did not return. End with the recurring
     propose_scheduled_delivery offer only if the skill is a scheduled digest:

Steps, in order:
1) <Resolve scope / inputs.>
2) <Fetch with read-only tool calls — all independent reads this turn.>
3) <After the results are in, compose the human-readable report (light
   markdown, chat-like; say so in one line when the result set is empty).>
4) <Deliver / offer schedule. NEVER call propose_scheduled_delivery as the
   first or only tool — steps 1–3 must have run first.>
-->

<!-- Multi-step skills must also include the Plan block below (house UX
     style). Progress lives in the update_plan tool — the shell renders the
     checklist as a pinned overlay and re-injects it every turn as the
     CURRENT PLAN block, so it survives ask_user_choice turn boundaries where
     prose headers vanish. Never ask for hand-emitted "### [n/N]" step
     headers. See onboarding-github-ci/a-analyzing-github-ci-performance for a
     filled-in example:

Plan (multi-step skills):
- On entry, before the first workflow tool call, call update_plan with the
  skill's fixed steps verbatim, the first step in_progress, and a one-line
  explanation (not a diagnosis; no hypothesis table).
- When the request already fixes an input (repo named, already verified, …),
  omit the skipped steps from the plan instead of renumbering.
- After a step's tool results, call update_plan marking it completed and the
  next step in_progress, in the same response as the next step's tool calls.
  When the next step is an ask_user_choice, mark the step and call the menu
  in the same response, then end the turn.
- Do not narrate the plan or repeat step names in prose; the shell renders
  the checklist.
-->

Compact examples:
1) "<literal user request>"
   → <tool>(<args>)
2) "<another phrasing covering a different input shape>"
   → <tool>(<args>)   [note same-turn vs next-turn where it matters]
