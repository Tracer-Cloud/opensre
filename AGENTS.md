# OpenSRE agent instructions

Keep this file to repository-wide rules and task-specific reading directions.
Before editing, read applicable package `AGENTS.md` files; load linked references
only for the work they govern. Put explanations and incident history in references,
not here.

## Human-owned files (mandatory)

Agents must never create, edit, rename, move, or delete any `SKILL.md` anywhere
in this repository or `core/agent_harness/prompts/opensre_system_prompt.md`,
including frontmatter. A direct request, failing check, or trivial change does
not override this rule. Otherwise, only suggest changes in chat or the PR
(quote current and proposed text), leave the file untouched, and report blocked
work while delivering everything else.

**Sole exception — literal Ctrl-H replacements.** An agent may apply an exact
find-and-replace of a word or sentence when the user supplies both the current
text and the replacement text verbatim. The replacement is absolute: swap the
given string for the given string, character for character, and touch nothing
else — no rewording, no reflowing, no "while I'm here" fixes, no frontmatter
bumps unless the user spelled those out the same way. If the old text is
ambiguous, missing, or the user described the change rather than dictating it,
the exception does not apply; fall back to suggesting.

Skills guide model reasoning, not deterministic pipelines. Supporting scripts
may handle only a few narrow mechanical chores, declared in
`references/script-tools.md`; decisions, branching, and end-to-end workflows
must not move into those scripts. Suggest a real tool when needed.

## Commands and delivery

- Install the environment and push hook: `make install`.
- Run this checkout: `uv run opensre …` from the repository root.
- Run Python with `uv run python …`.
- Follow [CI.md](CI.md) for scoped local validation, every push/PR, and CI/review
  follow-through. Do not add broader local validation requirements.
- Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md), including AI disclosure.

## Repository-wide conventions

- Use strict typing, one clear purpose per module, and concise contract-focused
  docstrings. Preserve non-obvious invariants and rationale, not bug history.
- Keep `__init__.py` files to public imports and `__all__`; implementation and
  side effects belong in focused modules.
- Protocol methods added or changed have a docstring as their **only** body:
  no `...`, `pass`, or `raise NotImplementedError`. Do not mass-convert unrelated
  existing stubs or trust a clean typecheck/CodeQL scan to enforce this rule.
- Use `http.HTTPStatus` constants, never numeric HTTP status literals, in source
  and tests.
- Env-var names and shared static constants belong in domain leaf modules under
  `config/constants/`, re-exported by its `__init__.py`. Only names used solely
  inside `config/llm_settings.py` may live there; do not create auth import cycles.
- Put behavior in its owning domain/provider/vendor module. Check sibling
  patterns first; keep orchestration and shared dispatch thin, registries limited
  to metadata/defaults, and shared UI generic. Two or more related feature-specific
  functions belong in a dedicated module. Do not add provider-specific branches
  to files serving multiple providers; delegate to a dedicated module instead.
- Follow architecture boundaries: `core/` and `integrations/` never import from
  `surfaces/`. Break import cycles with shared leaf modules, not lazy imports.
- After refactoring, migrate imports/tests and remove compatibility-only
  forwarding modules in the same change; keep one canonical import path.
- Never hide failing behavior behind constant-condition toggles or skip tests
  to conceal failures.
- Natural-language intent belongs to the action agent: no regex/keyword/fuzzy
  routing or deterministic bypasses. The interactive-shell planner never denies
  turns; do not restore `mark_unhandled` or `UNHANDLED:`. Follow the applicable
  harness/shell instructions for explicit-command exceptions and handoff tags.
- Redact exception details at external HTTP/chat response boundaries, not shared
  call sites. Log full detail server-side with `logger` and `capture_exception`;
  return a generic message or exception type name. Local terminal output may
  retain detail, but external responses must not expose provider/model internals.
- Cached LLM clients serve concurrent turns. Retry decisions must use facts
  captured from the current request, not mutable shared client flags.

## Tests, UI, docs, and performance

- Prefer one high-signal test per failure class: security/isolation, concurrency,
  crash/shutdown, package/transport boundaries, and real regressions. Thin redundant
  mock-call, vocabulary, success, and control-flow-only tests unless they are the
  only coverage of a contract; exercise real loops/wiring where relevant.
- Before behavior-preserving refactors, add a characterization test and confirm
  it passes on the original code; keep it green through the change. Optimize
  afterward and include any benchmark in the PR.
- On hot paths, use sets/maps for repeated membership/lookups, reuse resolved
  values, avoid repeated `deepcopy`/serialization/sorting, and verify cached
  objects are not mutated. Leave cold code simple.
- CLI/UI inputs must show required/optional fields and defaults, validate forms
  and direct commands consistently before saving, and preserve values on errors.
- `docs/` is user-facing: include actions, required/optional values, commands,
  env vars, and invisible gotchas—not internal APIs or bug history. Add new
  `.mdx` pages to `docs/docs.json` so they appear in navigation.

## Read when relevant

| Work | Required reference |
| --- | --- |
| Package structure or ownership | [Architecture and repo map](docs/ARCHITECTURE.md); [core naming](docs/NAMING.md) for `core/` changes |
| Tools or integrations | [Placement policy](docs/tool-placement-policy.md) and [contributor guide / definition of done](docs/adding-tools-and-integrations.md) before adding or approving one; import contracts via `core.tool` and authoring helpers via `core.tool_framework` |
| Persistence or migrations | [PERSISTENCE.md](PERSISTENCE.md) |
| CLI, REPL, or shared-terminal presentation | [Shared terminal UI rules and visual verification](surfaces/interactive_shell/ui/AGENTS.md) |
| Agent execution or action policy | [Harness rules](core/agent_harness/AGENTS.md), [shell rules](surfaces/interactive_shell/AGENTS.md), and [action policy](docs/interactive-shell-action-policy.md) |
| LLM providers, retries, or tool schemas | [Hosted LLM rules](core/llm/AGENTS.md) and [request/schema notes](CONTRIBUTOR_NOTES.md#llm-requests-and-tool-schemas) |
| Test fakes or test typing | [Test notes](CONTRIBUTOR_NOTES.md#test-fakes-and-typing) |
| CodeQL/code-quality findings | [Safe remediation notes](CONTRIBUTOR_NOTES.md#codeql-and-code-quality) |
| Human skill/prompt proposals | [Prompt rules](core/agent_harness/prompts/AGENTS.md) and [skill contract](core/agent_harness/prompts/skills/AGENTS.md); these do not authorize edits to protected files |
