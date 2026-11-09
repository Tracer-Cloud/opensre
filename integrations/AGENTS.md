# Integrations

`integrations/<vendor>/` owns everything about one vendor: config normalization,
verifier, clients, helpers, catalog/store wiring, and the vendor's tools
(`integrations/<vendor>/tools/`). Read
[docs/adding-tools-and-integrations.md](../docs/adding-tools-and-integrations.md)
for the full definition of done before adding one.

## Tool-usage SKILL.md cards are human-owned

`integrations/<vendor>/tools/**/SKILL.md` cards are **never** to be created,
edited, renamed, moved, or deleted by an agent — no exceptions for frontmatter
bumps, test fixes, or direct requests. An agent may only **suggest** a change
(current text → proposed text) in its reply or PR description and leave the
file untouched for a human to apply. Same rule as the root `AGENTS.md`.

Skills are natural-language cards, not deterministic tools. Deterministic
vendor behavior — API calls, parsing, retries, decision logic — belongs in a
real tool under `integrations/<vendor>/tools/`, which a skill then describes
how to use. Do not push that logic into a skill's supporting scripts; a skill
may carry only a few small helpers, and none that replace the model's
reasoning. Full contract: `core/agent_harness/prompts/skills/AGENTS.md`.

## Setup flow — how a vendor becomes "configured"

Setup runs on three surfaces — `opensre onboard` (wizard),
`opensre integrations setup <vendor>` (CLI), and the interactive-shell action
tools. They differ only in how they **collect** values; what happens afterwards
is identical and lives in one place: [`setup_flow.py`](setup_flow.py).

- **Declare an `IntegrationSetupSpec`** in `integrations/<vendor>/setup.py`
  (fields + verifier), then hand collected values to `apply_setup()`. It
  verifies, optionally resolves, and persists to **every tier** — the
  integration store, the keyring (secrets), and the project `.env`.
- **Never hand-roll `upsert_integration` + env writes in a surface handler.**
  That store-only vs. three-tier split is exactly the bug this module exists to
  prevent: runtime resolves the store first and hides it, but the deploy
  preflight reads `.env` and sees a half-configured integration.
- **`SetupField`** carries `name`/`label`/`prompt`/`env_var`/`default`/
  `required`/`secret`/`constant`. The tier is derived from `env_var`
  automatically (`*_TOKEN`/`*_KEY`/`*_PASSWORD` → keyring, else `.env`); a field
  never chooses its own tier.

## Cross-field rules go in the verifier, not a setup hook

A rule that spans several fields — either/or, XOR, all-or-nothing — belongs in
`integrations/<vendor>/verifier.py`, **not** a setup-only check. There is
deliberately **no `validate` hook** on the spec.

Why: `opensre integrations verify <vendor>` (health checks) calls only the
verifier. A setup-only check would let setup and health checks disagree on what
"configured" means. Enforcing the rule in the verifier gives one definition,
exercised by both.

- Precedent: `verify_rocketchat` rejects an incomplete "webhook **or** the
  server-url/token/user-id trio".
- A UI picker is **not** a validation guarantee — an agent or a hand-edited
  store bypasses it, so the verifier must still reject bad combinations.

## Mode pickers (`SetupMode`)

When a vendor has mutually-distinct config paths (an auth method; webhook vs.
Socket Mode), declare `SetupMode`s + `mode_prompt` on the spec instead of
prompting every field flat. The picker scopes which fields are collected; fields
outside the chosen mode are **cleared, not prompted**, so choosing one mode turns
the others off. The verifier still enforces the real combination (see above).

## Pointers

| File | What |
| --- | --- |
| [`setup_flow.py`](setup_flow.py) | The contract: `apply_setup`, `SetupField`, `SetupMode`, and the `resolve`/`finalize` hooks |
| `<vendor>/setup.py` | The per-vendor spec (e.g. `rocketchat`, `telegram`) |
| `<vendor>/verifier.py` | Verification **and** cross-field rules |
| [docs/adding-tools-and-integrations.md](../docs/adding-tools-and-integrations.md) | Full checklist / definition of done |
