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
[`skills/onboarding_cicd_fix/SKILL.md`](skills/onboarding_cicd_fix/SKILL.md):

- Start with a `#` title and a short statement of purpose.
- Use descriptive `##` sections and `###` workflow steps where order matters.
- Write direct instructions in short paragraphs or lists; avoid decorative
  banners, all-caps section labels, and repeated tool inventories.
- Keep commands and exact output examples in inline code or fenced blocks.
- Preserve frontmatter, tool contracts, exact choice labels, authorization
  boundaries, and required output formats when changing presentation.
- A static menu a skill always opens on entry belongs in `pre_execute`
  frontmatter (`tool: ask_user_choice` + `args`), not in prose the model must
  replay; the host runs it before any model step (see `onboarding_cicd_fix`).
