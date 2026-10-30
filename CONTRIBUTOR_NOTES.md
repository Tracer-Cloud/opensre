# Contributor notes

Read the relevant section when changing test fakes, LLM requests, or remediating
CodeQL findings. These notes supplement [AGENTS.md](AGENTS.md); validation and
review follow-through remain governed by [CI.md](CI.md).

## Test fakes and typing

Do not inline a lambda that builds an ad-hoc `type(...)` object or nests another
lambda in `monkeypatch.setattr` / `patch`. Extract a named helper instead:

```python
def _build_harness() -> Any:
    return type("H", (), {"resolve_env_variables": lambda _self: None})()


monkeypatch.setattr(startup, "_build_harness", _build_harness)
```

Trivial lambdas such as `lambda **_kw: None` or `lambda: sentinel` may stay inline.

`make typecheck` covers product packages, not `tests/`. It does not establish
that new tests are type-clean; run mypy on the test path directly when it matters.

## LLM requests and tool schemas

- Tool schemas must be normalized in the provider adapter and covered by
  registry contract tests. Loose JSON Schema checks can pass schemas that fail
  at the LLM API, where all available tools are sent together.
- LLM clients are cached per role and serve concurrent gateway turns. Capture
  request-local facts when building a request and consult those facts on retry;
  another in-flight request may already have changed a shared flag.
- For example, a prompt-cache fallback must check whether the failing request
  carried cache markers, not whether the client still has markers enabled.
  Reproduce this race deterministically by having the fake dependency mutate
  shared state before raising; threads are not required.

## CodeQL and code quality

Preserve behavior when addressing an alert. Do not replace useful work with a
no-op or suppress a finding instead of fixing its cause.

| Finding | Safe remediation |
| --- | --- |
| `py/cyclic-import` | Move shared symbols to a leaf module. Function-local and `TYPE_CHECKING` imports still count as cycle edges. |
| `py/uninitialized-local-variable` / unreachable code | CodeQL does not model `NoReturn` helpers such as `pytest.skip`, `pytest.fail`, `sys.exit`, or `typer.Exit`. Bind values on every visible path; prefer `next(iterable, None)` and an explicit guard over `StopIteration` control flow. Extract a `_raise()` helper for a bare-`raise` reachability alert. |
| `py/ineffectual-statement` on a Protocol | Follow the root docstring-only rule. A clean scan or mypy result is not proof of compliance: `pass` and `raise NotImplementedError` bodies are not caught. |
| `py/ineffectual-statement` on `await` | Keep the await: reaping a cancelled task may be what closes its client. Bind the awaited result in a small helper rather than deleting the await. |
| `py/implicit-string-concatenation-in-list` | Extract long text into a constant and reference it from the list/tuple. Do not silence the alert with explicit `+`; implicit concatenation in a parenthesized assignment is fine. Shared constants still follow the root ownership rule. |
| `py/import-and-import-from` | Follow the file's existing import style, including inside functions; do not mix module imports and `from` imports for the same module. |
| Catching `BaseException` | Catch `Exception` for ordinary failures; do not swallow `KeyboardInterrupt` / `SystemExit` or retain `noqa: BLE001` to hide the issue. |
| Unused global variable | Cross-module imports may not be credited as uses. Keep related copy in a genuinely used structure, or co-locate a private constant with its sole reader. Do not add no-op self-references or `# noqa`; shared constants still belong under `config/constants/`. |
| `py/stack-trace-exposure` | Apply the root external-error rule at the sink/response boundary. Log details server-side; never send exception text, arguments, tracebacks, or provider/model/field internals to HTTP or chat gateway users. |

## Human-owned prompt changes

Humans should edit one skill card or prompt section per commit through a web
editor and review the resulting diff themselves. Agents may propose exact
current/replacement text, but must leave protected files untouched except for
the sole literal-replacement exception in [AGENTS.md](AGENTS.md#human-owned-files-mandatory).


## Connection journey audit (#6936)

This section records the current connection paths and the first incremental
alignment target. It is contributor-facing design context, not a claim that
profile-level multi-account selection or every guided-setup surface is already
implemented.

### Current entry points and behavior

| Surface | Current path | Important boundary |
| --- | --- | --- |
| Interactive `/login` | `login_cmds.py` selects list/connect/verify/disconnect; connect delegates to `auth_menu.py` and then the canonical login command. | The original picker first asked for Subscription, API key, CLI-managed, or Local/cloud, so a user had to know the auth category before seeing related provider routes. |
| CLI `opensre login` | `surfaces/cli/commands/login.py`; direct `opensre login <provider>` bypasses the picker. | `connect` persists provider access; it does not activate local inference by default. `--set-provider` is the explicit activation option. |
| Interactive `/model` and CLI `opensre model` | `model/command.py`, `surfaces/cli/commands/model.py`, `model_picker.py`, and `model_choices.py`. | The picker uses cached models and saved connection metadata; it does not launch vendor CLIs, sign in, or perform live verification. Its current key is `provider:model`, so it distinguishes provider routes but not multiple account profiles under one provider. |
| Connection status | `opensre login list/status` and `/login list` render provider status from local metadata. | Status and verification are separate: status is prompt-safe; `verify` intentionally checks the route's credentials. |
| Subscription accounts | `subscription_service.py` and the provider adapters. ChatGPT accepts `--profile <registration-id>` and `--new-account`; its registration store keeps account registrations and one active token profile. | The normal model picker receives one provider-level connection. It does not currently offer multiple saved ChatGPT registrations as separate selectable connections. |
| Vendor CLIs | `catalog.py` registers CLI routes; `integrations/llm_cli/` owns vendor detection and invocation. | OpenSRE stores non-secret setup metadata; the vendor CLI owns its own session/configuration. An installed CLI is not proof of a usable authenticated account. |
| `opensre onboard` | `surfaces/cli/wizard/flow.py` and `llm_credential.py` use `WizardCredentialKind` for API keys, CLI, local host, cloud identity, and subscription setup. | The wizard has its own credential vocabulary and setup flow; it is not yet backed by the shared connection summary. |
| OpenSRE account | `/account` and `surfaces/cli/commands/account.py`. | OpenSRE identity and hosted inference / credits are independent of external provider connections. Account logout is not provider disconnect. |

### Existing disconnect ownership

- API-key disconnect removes OpenSRE provider metadata and OpenSRE-managed key
  material from the supported local stores / environment file.
- Subscription disconnect calls the provider adapter's logout path. For
  ChatGPT, the selected token session is cleared; the saved registration is
  retained. Remote revocation may remain unconfirmed.
- CLI, host, and ambient cloud routes remove only OpenSRE setup metadata.
  External CLI sessions, ambient credentials, and host settings are left in
  place.
- OpenSRE account logout is a separate operation and must not be folded into
  provider disconnect.

### Proposed journey and invariants

1. Start from a searchable catalog of all supported provider routes. Keep
   entries distinct (for example, OpenAI API and Codex CLI); do not force users
   to pick an authentication-method category first.
2. Show route facts independently: provider/service, route, safe account label
   when one exists, authentication owner/mechanism, execution path, billing
   source, and current availability. For vendor CLIs, use an honest unknown
   billing state unless the adapter can prove a specific billing route.
3. Keep connect, verify, select model, and disconnect as separate actions.
   Connect persists access only; verify may perform an explicit check; model
   selection is the only operation that changes the active provider/billing.
4. Scope model choices to the exact route and account registration. Duplicate
   model names on different routes or registrations must stay distinct.
   Cancellation or a failed selection must leave the active route unchanged.
5. Keep saved but stale/unverified connections visible with a route-specific
   recovery action. Rendering menus must not trigger sign-in, inference, or
   expensive vendor/cloud probes.
6. Explain disconnect ownership before destructive actions. OpenSRE may remove
   only OpenSRE-owned credentials; vendor CLI sessions and ambient cloud
   credentials remain externally managed unless the user follows the vendor's
   own cleanup steps.
7. Preserve OpenSRE account identity and hosted-credit routing independently;
   never silently fall back between provider subscription, API usage, and
   OpenSRE credits. Reuse existing provider IDs, aliases, env/config keys, and
   the local/cloud/custom-endpoint onboarding options.

### Known follow-up gaps

- ChatGPT registrations are stored separately, but connection/model selection
  remains provider-scoped today. A profile-aware connection identifier needs a
  dedicated design and regression coverage before exposing multiple accounts
  in one picker.
- The onboarding wizard still has its own `WizardCredentialKind` and is not yet
  consuming the shared summary; its local, cloud, and custom endpoint paths
  must remain available during any later alignment.
- Full TTY verification (CLI/REPL parity, Esc/back navigation, narrow-terminal
  rendering, and next-prompt input) is required before treating the UX work as
  complete.
