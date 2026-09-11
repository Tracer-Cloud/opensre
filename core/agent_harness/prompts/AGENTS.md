# prompts/ — single-agent prompt assembly

## Layout

| Package | Role |
|---------|------|
| `kernel/` | `PromptEnvelope` / tiers / `SurfaceProfile` — no agent-path knowledge |
| `grounding/` | Prompt-side grounding providers (`DefaultPromptContextProvider`) that feed assemblers — distinct from harness `grounding/` caches |
| `action/` | Tool-calling agent prompt assembly and policies |
| `memory/` | Conversation window + prior-investigation recall |
| `runtime_facts/` | Runtime-metadata fact lines for prompts |
| `skills/` | Progressive skill index + markdown bodies (`loader.py` + `*.md`) |
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
- A mid-flow menu the model must not skip belongs in `after_tool` (same call
  shape, plus `after:` the trigger tool). The host opens it after that tool
  succeeds; later tools in the batch are blocked once a menu is queued.
- Rules shared by sibling skills belong in a markdown file listed under
  `references:` (resolved inside the skills tree), not copied into each body.

## Skill metadata ownership

Every `SKILL.md` frontmatter `metadata` block records two people:

- `owner` — the person who created the skill. Use their name, never a team
  label such as `Tracer Team`. Set it once at creation and do not change it
  when someone else edits the skill later.
- `last_changed_by` — the name of the person who most recently changed the
  skill.
- `last_changed_at` — the ISO date (`YYYY-MM-DD`) of that change.

`last_changed_by` and `last_changed_at` move together: whoever edits a skill
(body or frontmatter) must update both lines in the same change; a skill edit
that leaves either behind is incomplete.

```yaml
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-09
```

Full rules: [`skills/AGENTS.md`](skills/AGENTS.md).

When you touch a skill that still carries a team label in `owner`, replace it
with the original author's name if you know it; otherwise leave it and note it
in the PR description rather than guessing.
