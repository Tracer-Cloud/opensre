# Unified provider login and model selection implementation plan

## Implementation status — 2026-10-27

Implementation branch: `codex/unified-login-model-ux`. The implemented slice covers the shared
connection/selection operations, method-grouped login pickers, direct cross-provider
model picker, login-to-picker handoff, choose-later semantics, inactive provider
settings, hosted/local switching, standalone model commands, and CLI/REPL documentation. Source IDs remain unchanged.
The Claude subscription PR has not been imported or modified.

**Still pending:** migrate the full onboarding wizard to the new UX without losing
its session-only, unverified, deferred, endpoint and binary-path recovery flows;
live provider/account validation; and CI/review approval. The existing
wizard is retained, with its regression tests passing. Do not treat this local
slice as completion of every acceptance item below.

Local terminal evidence and the test log are in `/tmp/opensre-model-qa/`. All
credentials used for these checks are synthetic; no live sign-ins or billed
inference were performed. This document remains the follow-up implementation
handoff as well as the original agreed product contract.

## Handoff and objective

Implement a simpler model setup experience in Tracer-Cloud/opensre in a **new
pull request against current upstream `main`**. Reference issue
[#6884](https://github.com/Tracer-Cloud/opensre/issues/6884). Do not merge the PR.

The user-approved direction is two entry points into one continuous journey:

- `/login` connects a provider, then automatically opens its model picker.
- `/model` opens model choices from connected providers immediately.
- Selecting a model explicitly changes the active provider/model. Authentication
  alone does not change those settings. Users never need to type `/model` after
  completing interactive login.
- “Connect a provider” inside the model picker invokes the same login workflow
  and returns directly to that provider's models.

This file is a portable implementation handoff. Paths below are relative to the
repository root. Planning baseline: `main` at `b922d8967`, inspected 2026-10-27.
Recheck current source and instructions before implementing; do not reset or
overwrite unrelated work to reproduce this baseline.

### Related Claude Code work

[#6879](https://github.com/Tracer-Cloud/opensre/pull/6879),
“feat(llm): connect Claude subscriptions through the official CLI”, was open at
planning time, with head `e79743eb3`. It adds a subscription-only connection
using the existing Claude Code CLI adapter. The existing `claude-code` adapter
on main already accepts subscription or API authentication, but does not promise
subscription-only billing.

Build this foundation independently on main. Keep current provider IDs and
authentication stores. Do not cherry-pick the entire Claude PR, replace its
branch, introduce a direct Claude.ai API/OAuth route, or merge its work. Once
the shared workflow exists, that PR can adopt it as a separate follow-up.
If it merges before implementation, retain its subscription safeguards and wire
its registration into this workflow without adding a second implementation.

## Product contract

### Model picker

Bare `/model` opens a direct, searchable model picker, not a subcommand menu or
a provider picker followed by a model picker. Use the shared terminal input
component with a single choice field and `submit_on_choice=True`; choosing a row
must not require a second submit or confirmation. Show the active selection,
preselect its exact row, and give each row a provider/source label.

Illustrative layout, not an entitlement claim or required exact model catalog:

```text
Choose a model
Current: Sonnet · Claude Code · CLI-managed

> Sonnet          Claude Code · CLI-managed       current
  Opus            Claude Code · CLI-managed
  GPT-6.1 Sol     ChatGPT · Subscription
  Saved API model OpenAI API · API billing

  + Connect a provider
```

- Row identity is the exact `(provider_id, model_id)` pair, not the display label.
  Do not deduplicate the same model across differently billed providers.
- Order the active provider first, then other providers in shared catalog order;
  retain provider-defined model order and keep the active custom model visible.
- Known API/CLI options are choices, not proof of account entitlement. Subscription
  account catalogs, Azure deployments and local installed models use their
  existing discovery implementations. CLI default remains a valid explicit row.
- Offer one “Custom model…” action only when supported. It collects the provider
  and exact model ID through the shared input component; do not remove current
  custom-endpoint or direct-command capabilities.
- Include one “Connect a provider” action. No top-level connection manager,
  separate activation screen, or additional “choose model now?” question.
- Empty state: “No providers connected” with the connect action. A broken provider
  must not prevent other providers' models being selected; show an actionable
  reconnect/retry state rather than treating it as authenticated.
- Successful selection exits with a short “Now using <model> · <source>” result.
  Esc/Ctrl-C leaves the active selection unchanged.

### Login and the post-login handoff

Login uses the shared subcommand tray UI (`repl_choose_subcommand`), not the
search/filter input form: choose a connection method, then a provider in its
submenu. Methods are **Subscription**, **API key**, **CLI-managed**, and
**Local / cloud** (host + ambient). Use ↑/↓ and one Enter per step, without an
extra confirmation. Esc from providers returns to methods;
Esc from methods cancels. Direct provider commands bypass both pickers. `/model`
remains a flat, direct cross-provider model picker, not a method submenu.

Group by structured auth kind, never by free-text descriptions. General Claude
Code stays CLI-managed; a separately enforced subscription registration belongs
under Subscription. Distinguish billing/auth without exposing implementation
slugs as primary labels. Reuse supported sessions instead of requiring a new
browser sign-in. Preserve provider-specific eligibility and login behavior.

After successful authentication, open the same model picker, scoped to the
provider just connected. Prefer the active model if it belongs to that provider;
otherwise preselect a valid provider default, then the first returned choice.
Preselection never means automatic activation, even for a single model.

The scoped picker has “Choose later, keep current model”. Esc has the same
meaning: keep the completed connection, but do not write active provider/model
settings. When login originated inside `/model`, choosing later returns to its
outer picker with updated choices. Cancellation before authentication succeeds
does not claim a connection was saved. Never undo an external vendor CLI sign-in
by silently running logout on cancellation.

Authentication necessarily saves or refreshes credentials. Reconnecting an
already-active provider can refresh those credentials; do not claim this is
credential rollback or identity isolation. Reuse the existing account by default.
Existing explicit account/profile options remain explicit and must explain when
they change the account used by that provider. Do not add automatic account
switching or a new multi-account selection system in this PR.

### Provider and billing safety

- General `claude-code` displays “CLI-managed”, not a guaranteed subscription
  route. Only a registered, enforced subscription connection may carry the
  subscription label. Group Claude routes coherently in the login presentation
  while retaining their exact internal identities and distinct billing policies.
- Do not parse free-text auth descriptions to decide billing or routing. Use
  structured provider/auth metadata; display unknown CLI billing honestly.
- Never fall back from subscription authentication to an API key, another account,
  hosted credits, or another provider because login/model access failed.
- Signing in to OpenSRE and selecting its hosted inference are separate concerns.
  Allow an explicit local model selection while retaining OpenSRE identity.
  Keep the hosted choice discoverable when the account route is available, so
  users can explicitly return to it without another login. Capture that choice
  before switching local; do not infer hosted availability solely from the
  currently effective route. Hosted model/credits policy itself is unchanged.

## Public command behavior

Add a thin standalone `opensre model` Click command over the shared operations.
Do not add a competing `model connect`/`model manage` command family for v1.

| Entry point | Required behavior |
| --- | --- |
| `/login`, `/auth login`, interactive `opensre auth login` | Connect, then open the provider-scoped model picker; switch only after selection. |
| `/model`, interactive `opensre model` | Open the cross-provider model picker directly. |
| `/model list`, `opensre model list` | Read-only listing of the same choices, source, selection and availability. |
| `/model show`, `opensre model show` | Read-only effective selection and source. |
| `/model set <provider> [model]`, `opensre model set <provider> [model]` | Explicit selection; no hidden login or picker when arguments are complete. Missing credentials give the correct login command. Omitted model uses the existing validated provider-default semantics. |
| Existing restore/toolcall commands | Preserve behavior and add matching standalone subcommands over the shared operations. |

Retain existing slash aliases and model-only shorthand. Preserve
`--toolcall-model` support. Keep root JSON conventions: `opensre --json model list`
and `opensre --json model show` emit structured data, not interactive output. Include exact provider
and model IDs, source label, active state and availability in each model row;
never include credentials. No speculative new REST/MCP API is required.

### Auth flag compatibility

Use a tri-state activation option so explicit flags remain distinguishable from
the default interactive handoff:

- Neither `--set-provider` nor `--no-set-provider`: interactive login opens the
  picker; non-TTY login connects only and prints the explicit selection command.
- `--no-set-provider`: connect only, skip the post-login picker in all contexts.
- `--set-provider`: intentional combined setup/selection. With `--model`, validate
  and select that model directly. Without it, interactive callers get the picker;
  non-TTY callers use a validated provider default or fail clearly if none exists.
- `--model` without explicit activation supplies the connection validation model
  and picker preselection, not permission to switch the active provider/model.
- No TTY means no picker, hidden key prompt or browser launched implicitly. Keep
  intentional provider-supported remote/device authorization available through
  existing explicit auth options and printed URLs. Required missing input fails
  with actionable usage, rather than reading stdin indefinitely.

The non-TTY default change is intentional. Document it prominently with
`--set-provider --model <id>` as the migration for scripts expecting auto-switch.
Keep existing validation, browser, profile and account options otherwise intact.

## Shared implementation and persistence

Keep shared setup behavior in `surfaces/shared/llm_setup/`; CLI and REPL handlers
parse arguments, own terminal lifecycle and render results. Do not make CLI
commands import REPL session/command modules or call the entire onboarding wizard.

### Reuse and extract

1. Keep `configure_subscription()` and the existing API-key validation/secret
   storage as the canonical provider-auth building blocks. Move reusable login
   presentation out of CLI command modules into shared setup presentation.
2. Extract non-UI model-selection validation/commit logic from the REPL switching
   helpers into a shared selection service. Return typed results/errors instead
   of printing from the service. Migrate REPL runtime/tool ports, public imports
   and tests to the canonical implementation; do not leave forwarding-only modules.
3. Add a small shared coordinator for connect -> optional model selection. It
   delegates provider-specific work rather than adding vendor branches to shared
   dispatch. Reuse current provider catalogs; do not build a new plugin framework
   or merge intentionally distinct wizard/runtime credential enums.
4. Add typed connection and model-choice results carrying exact provider/model
   identity, source, availability, available choices and whether selection changed.
   Extend existing result types where useful; secrets must not enter UI results,
   metadata, logs, JSON output or cache keys.

### Authentication ordering and model catalogs

Subscription adapters currently accept a `select_model` callback before committing
an account. Do not remove that safety ordering accidentally. For connect-only
authorization, supply a non-interactive callback that checks an explicit requested
model or chooses a valid default/first account-visible model, captures the catalog,
and returns it for the provider's existing auth transaction. This is an internal
validation choice, not activation. The user-facing picker runs after connection
commit, so choosing later can retain the connection. Empty or invalid catalogs
fail setup without selecting an invalid account/model.

Use static catalogs for ordinary API/CLI choices plus a provider-scoped saved
custom model. Cache discovered non-secret subscription choices in the existing
provider auth metadata, with a central accessor for parsing/validation; never
store OAuth tokens there. Retain one catalog snapshot per provider, not a new
connected-provider database. Ordinary metadata/refresh writes preserve the snapshot;
reconnect replaces it and logout clears it.

Picker construction uses prompt-safe status and cached/curated choices. Discovery
requiring credentials/network happens during intentional setup or explicit refresh,
not during every redraw. Reuse provider-owned Azure/Ollama discovery, keep it
bounded/cancellable, and isolate provider failures. Cached choices are not current
entitlement guarantees; revalidate through the existing provider rules on selection
and enforce access at inference. Do not add a paid API probe to every model switch.

Inventory must recognize existing environment keys, auth metadata and supported
CLI sessions without requiring users to log in again. Preserve CLI's three-state
auth semantics: installed with unknown auth is not “verified”; keep configured
or active CLI choices visible with “Auth checked on use” where existing runtime
permits them. Missing binaries/definite sign-out offer connection recovery.

### Separate saved provider configuration from active selection

The current environment sync removes other providers' non-secret model/endpoint
settings. That would make a cross-provider picker unreliable. Preserve inactive
provider-specific model/endpoint configuration when changing the active selection;
continue normalizing truly global active transport/routing settings on each switch.
Keep credentials in their existing stores, with current environment precedence.
Do not introduce new credential storage or silently remove environment overrides.

Connecting persists credentials and required provider settings without changing
`LLM_PROVIDER`, the active model, active transport or the hosted/local choice.
Selection commits the full validated active choice through one shared operation,
reusing the existing env/setup-store writers. Validate before writing; do not save
a new active selection on invalid input, failure or cancellation. Preserve existing
unverified/session-only/deferred recovery outcomes and label them accurately.
Session-only credentials must not be written to durable storage by the picker.

On in-process selection reset LLM clients. After a child CLI changes settings,
reload only the relevant saved selection/provider settings in the parent and reset
its clients. Connect-only login must refresh provider availability and newly saved
settings without importing an unrelated saved active selection. Cover environment
shadowing so the first request after a deliberate credential replacement uses the
intended credential. Do not reload unrelated environment exports or change routing
and billing based on free text.

Onboarding invokes the same connect and model-picker block directly, retaining its
existing probes, recovery choices, summary, analytics, starter-loop setup and shell
launch wrapper. No second provider/model pick after the shared flow finishes. If
setup is deferred and no model is usable, do not launch a broken inference turn;
retain the deferred summary and actionable next command.

### Terminal and agent lifecycle

Use existing `build_input_form`, `InputField`, `InputChoice` and
`run_command_input`, with a single searchable choice and submit-on-choice behavior
for models. Local form validation is side-effect-free; auth, discovery and commits
happen outside validation/redraw callbacks. Use existing record rendering for
read-only lists and existing domain-owned endpoint/auth forms for setup.

Keep exclusive-stdin reservations, prompt suspension, queued agent-selected
interactive commands and subprocess handoffs. Agent/headless `/model` is read-only
and never opens a picker against a live prompt; intentional interactive requests
use the established deferred-command path. Keep slash registry, catalog/MCP parity,
usage, completions, lazy CLI command registration and JSON behavior synchronized.

## Ordered implementation slices

Each slice must leave explicit commands usable. Add characterization tests and
run them against the original behavior before extracting existing logic. Changes
to defaults/pickers get new expectation tests rather than pretending they are
behavior-preserving refactors.

1. **Shared selection operations.** Extract validation/commit and migrate existing
   explicit `/model set`, model-only, restore, toolcall and tool-port callers.
   Verify current model/cache/persistence contracts before changing interactive UX.
2. **Connection without activation.** Separate provider setup from active-choice
   writes; preserve inactive settings; add shared results and catalog snapshots.
   Verify successful connection, retries, cancellation, subscription account safety,
   credential isolation and unchanged active routing.
3. **Direct model picker.** Build the shared list and one-choice UI; replace the
   bare `/model` subcommand menu. Wire source labels, active state, custom models,
   connect/choose-later actions and failure recovery. Verify one selection gesture.
4. **Login handoff and onboarding.** Route interactive login and onboarding through
   the shared coordinator. Update tri-state flags, parent refresh and non-TTY
   behavior. Preserve explicit legacy shortcuts and recovery outcomes.
5. **Standalone parity and delivery.** Register `opensre model` list/show/set/
   restore/toolcall commands, update catalog/help/completions and user docs, perform
   PTY verification, and publish a new PR against main using the repository template.

Do not include provider-ID migration, a general connection manager, native-vs-CLI
transport replacement, new dependencies, unrelated effort/footer redesign or live
subscription billing experiments. Existing account/profile operations remain; a
new multi-account model picker is out of scope.

## Verification and acceptance

Prefer high-signal regressions for the shared contract rather than repeated mocks
for every wrapper. Use isolated homes, config paths, synthetic credentials and fake
vendor CLIs. Do not use real tokens or billed inference for automated UX checks.

- Login completes and immediately opens only the newly connected provider's models;
  selecting commits once. Choosing later/Esc preserves saved auth and the previous
  provider/model/hosted-local selection, including on restart.
- `/model` lists multiple configured sources directly, marks the exact active pair,
  switches in one selection gesture, and offers connect when empty. Identical model
  names on different billing routes remain distinguishable.
- Missing credentials, unavailable models, discovery failures and cancellation do
  not partially change active settings. One failing provider does not hide others.
- API-key, CLI, subscription, Azure/custom endpoint, local and ambient paths retain
  their supported setup/validation rules. Validation uses an explicitly requested
  model when provided, not an unrelated provider default. Unknown CLI auth remains
  distinct from definitely signed out. Secrets never appear in UI/results.
- API keys and subscriptions for the same vendor coexist without silent fallback.
  Switching away and back retains endpoint/model settings. Explicit local selection
  retains OpenSRE identity and allows an explicit return to hosted inference.
- A successful subscription login retains its existing account transaction safety;
  choose-later does not select another runtime provider/model. Explicit reconnect
  and account/profile changes are reported accurately, not described as rollback.
- Cover interactive default, `--no-set-provider`, `--set-provider` with/without
  `--model`, non-TTY connect-only default, missing inputs and JSON listings.
- Verify child login -> parent refresh -> first model request, plus cache reset
  after a switch. Test saved-key replacement and environment precedence explicitly.
- Session-only/unverified/deferred wizard recovery remains accurate. No double
  provider/model picker, nested REPL launch or persisted session-only secret.
- Real PTY journeys at 40, 100 and 160 columns include long/Unicode names, literal
  markup, empty/failed state, login handoff, cancellation, resize and typing into
  the next prompt. No CPR garbage, stolen keystrokes or hung child processes.

Useful existing focused test locations:

```text
tests/cli/test_auth_command.py
tests/cli/test_model_persistence_scenarios.py
tests/cli/wizard/test_model_selection.py
tests/cli/wizard/test_flow.py
tests/interactive_shell/test_commands.py::TestModelCommand
tests/interactive_shell/test_model_credential_entry.py
tests/interactive_shell/test_model_cache_reset.py
tests/interactive_shell/test_ollama_model_switching.py
tests/interactive_shell/command_registry/test_auth_login_env.py
tests/interactive_shell/command_registry/test_command_inputs.py
tests/shared/terminal/test_input_form.py
tests/config/test_llm_auth.py
```

Run narrow tests as slices change, for example:

```bash
uv run python -m pytest -q tests/cli/test_auth_command.py
uv run python -m pytest -q tests/interactive_shell/test_commands.py::TestModelCommand
uv run python -m pytest -q tests/cli/test_model_persistence_scenarios.py tests/interactive_shell/test_model_cache_reset.py
uv run python -m pytest -q tests/interactive_shell/command_registry/test_slash_catalog.py
```

Add focused tests for the new shared flow and standalone command alongside these
existing contracts. Run `make install` to provision the implementation environment
and hook, then follow `CI.md` for scoped checks, normal pre-push validation and
CI/review follow-through; do not invent a mandatory whole-repo local check.
Include actual synthetic PTY evidence in the PR's Demo section. If cloud execution
cannot exercise live vendor authentication, record that limit rather than claiming
subscription consumption, entitlement, renewal or billing acceptance passed.

## Cloud implementer checklist

- [ ] Read root and applicable package `AGENTS.md` files, `CI.md`, `PERSISTENCE.md`,
      architecture/naming rules and the PR template before editing their domains.
- [ ] Recheck issue #6884 and PR #6879 status. Work on a new `codex/` branch from
      up-to-date upstream main; preserve unrelated files and user work.
- [ ] Never edit any repository `SKILL.md` or
      `core/agent_harness/prompts/opensre_system_prompt.md`. If a protected-file
      change appears necessary, quote a suggestion and report the limitation.
- [ ] Implement and verify the slices above; keep one canonical implementation,
      provider-owned behavior and existing core/integration/surface boundaries.
- [ ] Update existing user-facing provider/REPL docs with the new flow, exact CLI
      commands, flags/defaults, script compatibility change and billing caveats.
      Add navigation if a new user-facing `.mdx` page is genuinely needed; this
      engineering plan is intentionally outside user-facing `docs/`.
- [ ] Publish the new upstream PR with the repository template and truthful AI
      disclosure, reference #6884, describe the #6879 integration boundary, and
      follow required CI/review loops. Do not merge without human permission.

Suggested handoff instruction:

> Implement UNIFIED_MODEL_SETUP_PLAN.md in Tracer-Cloud/opensre as a new PR
> against current main. Follow repository instructions and the ordered slices.
> Preserve the login-to-model-picker handoff, direct /model picker, explicit
> model activation and subscription billing boundaries. Keep existing provider
> IDs; do not fold PR #6879 wholesale into this PR or edit protected prompts or
> skills. Run focused tests and terminal verification, follow CI/review through,
> and report any live-auth checks that remain unverified. Do not merge.
