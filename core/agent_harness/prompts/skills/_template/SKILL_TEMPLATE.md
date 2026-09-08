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
   Fill the `metadata` block: `owner`, `usecases` (what a user asks for),
   `requires` (accounts, tokens, local state), `type` (onboarding, analytics,
   report, repair, audit), `version`, and `prerequisite_for` when another
   skill must run first. The loader ignores it; people and the docs read it.
6. Optional report template: a sibling file named <folder>_report.md is
   appended automatically to the body that skill_view returns.
7. Section order below is the house style (see github_ci_fix for a
   single-tool skill, architecture_audit for a multi-pass one). Keep the
   whole body tight — it is loaded into the planner's context on demand.
-->
---
name: <kebab-case-name>
description: >-
  <One or two lines for the compact index: what the skill does and the main
  tool(s) it uses. Add "Multi-step; load before acting." if data-dependent.>
metadata:
  owner: <person or team>
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

<!-- Multi-step skills must also include the step-labeling block below (house
     UX style), with N replaced by the skill's total step count. The terminal
     renders this narration live, turning the tool stream into a readable
     story instead of a wall of raw commands:

Step labeling rules (UX):
- Before every numbered step's tool calls, emit this exact header format as
  assistant text in the SAME response as the tool calls, then one short
  status sentence:
    ### [n/N] <step name>
    <One-sentence status or question.>
- Never start tool calls for a new step without its header.
- After a step's tool results are in, state its outcome in one line (start
  it with ✓ on success, ✗ plus what failed otherwise) before the next
  step's header.
- Reuse the step's own name from this skill as the phase name and its own
  number as n, even when a step is trivial or already satisfied (a ✓ line
  with no tool calls is fine); never renumber mid-run.
-->

Compact examples:
1) "<literal user request>"
   → <tool>(<args>)
2) "<another phrasing covering a different input shape>"
   → <tool>(<args>)   [note same-turn vs next-turn where it matters]
