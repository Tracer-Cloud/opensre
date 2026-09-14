# prompts/ — single-agent prompt assembly

## SKILL.md files and the system prompt: suggest only, never edit

Agents are **never** allowed to change a `SKILL.md` file — not the body, not
the frontmatter, not `version` / `last_changed_by` / `last_changed_at`, not a
rename or move, not a new card, not a deletion. The same applies to the system
prompt `opensre_system_prompt.md` in this directory: no edits to its text, no
rename, move, or deletion. The formatting and metadata rules below describe
what a **human** author must do; for an agent they are review criteria only.
When you believe a `SKILL.md` or the system prompt needs a change, write the
proposal (quote current text, give proposed text) in your reply or the PR
description and leave the file as it is. **Under no circumstance** may an
agent modify these files otherwise. The sole exception is a literal Ctrl-H
replacement: the user gives the exact current word or sentence and the exact
replacement, and the agent swaps one for the other character for character
with no other change to the file (see the root `AGENTS.md`).

## Skills are prose first, scripts last

A skill is primarily natural language: numbered steps the model reads,
reasons about, and executes with the normal tool catalog. Supporting scripts
are the exception — a few small helpers at most, each handling one mechanical
chore (collect, reshape, count) whose result the model interprets. A skill
must not become a deterministic tool in disguise: no script that decides what
the next step is, no script chain that runs the workflow end to end, no
vendor logic that should live under `integrations/` or `tools/`. When a
proposed script would replace the model's judgment, propose a tool or a prose
step instead.

## Layout

| Package | Role |
|---------|------|
| `kernel/` | `PromptEnvelope` / tiers / `SurfaceProfile` — no agent-path knowledge |
| `grounding/` | Prompt-side grounding providers (`DefaultPromptContextProvider`) that feed assemblers — distinct from harness `grounding/` caches |
| `action/` | Tool-calling agent prompt assembly and policies |
| `memory/` | Conversation window + prior-investigation recall |
| `runtime_facts/` | Runtime-metadata fact lines for prompts |
| `skills/` | Progressive skill index + markdown bodies (`catalog/` + `content/` + workflow Markdown) |
| `rules.py` | Shared rule fragments (leaf) |
| `system_prompt.py` + `opensre_system_prompt.md` | Loader and adjacent Markdown for the shared system base |

Root `__init__.py` is a thin facade for common imports.

## Dependency rule (acyclic)

```
kernel  ←  memory, runtime_facts, skills, rules, grounding, system_prompt
        ↑
      action
```

- Leaves may import `kernel` (and each other only when a clear owner exists).
- The action package may import leaves + `kernel`.

## Provenance

`PromptBlock.provenance` should name the owning module under this tree
(e.g. `core.agent_harness.prompts.opensre_system_prompt.md`).

## Skill body formatting

Use ordinary Markdown, following
[`skills/onboarding-github-ci/SKILL.md`](skills/onboarding-github-ci/SKILL.md):

- Start with a `#` title and a short statement of purpose.
- Use descriptive `##` sections and `###` workflow steps where order matters.
- Write direct instructions in short paragraphs or lists; avoid decorative
  banners, all-caps section labels, and repeated tool inventories.
- Keep commands and exact output examples in inline code or fenced blocks.
- Preserve frontmatter, tool contracts, exact choice labels, authorization
  boundaries, and required output formats when changing presentation.
- A static menu a skill always opens on entry belongs in `pre_execute`
  frontmatter (`tool: ask_user_choice` + `args`), not in prose the model must
  replay; the host runs it before any model step (see `onboarding-github-ci`).
- Describe mid-flow menus in the numbered workflow; the model calls
  `ask_user_choice` after completing the preceding step.
- Rules shared by sibling skills belong in a markdown file listed under
  `includes:` (resolved inside the skills tree), not copied into each body.

## Skill metadata ownership

Every `SKILL.md` frontmatter `metadata` block records two people:

- `owner` — the person who created the skill. Use their name, never a team
  label such as `Tracer Team`. Set it once at creation and do not change it
  when someone else edits the skill later.
- `last_changed_by` — the name of the person who most recently changed the
  skill.
- `last_changed_at` — the ISO date (`YYYY-MM-DD`) of that change.
- `version` — a quoted `MAJOR.MINOR` string; each edit adds one behind the
  dot (`"2.1"` → `"2.2"` → … → `"2.15"`). The major part moves only for a
  breaking change to the card's contract.

`last_changed_by`, `last_changed_at`, and `version` move together: whoever
edits a skill (body or frontmatter) must update all three lines in the same
change; a skill edit that leaves any behind is incomplete.

```yaml
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-09
  version: "2.2"
```

Full rules: [`skills/AGENTS.md`](skills/AGENTS.md).

When you touch a skill that still carries a team label in `owner`, replace it
with the original author's name if you know it; otherwise leave it and note it
in the PR description rather than guessing.
