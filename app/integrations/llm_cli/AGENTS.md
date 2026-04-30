# LLM CLI providers (subprocess)

Use this package when adding a new **non-interactive** LLM that shells out to a vendor CLI (like OpenAI Codex), instead of HTTP APIs.

## Layout


| File                 | Role                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `base.py`            | `LLMCLIAdapter` protocol, `CLIProbe`, `CLIInvocation`.                                      |
| `registry.py`        | `CLI_PROVIDER_REGISTRY`: maps `LLM_PROVIDER` → `adapter_factory` + optional `model_env_key`. |
| `binary_resolver.py` | Shared executable resolution helpers (`env -> PATH -> fallback paths`).                     |
| `runner.py`          | `CLIBackedLLMClient`: guardrails, `detect()`, `subprocess.run`, ANSI strip, `LLMResponse`.  |
| `text.py`            | `flatten_messages_to_prompt` for stdin from chat-style payloads.                            |
| `codex.py`           | Reference adapter: binary resolution, `codex exec`, probe via `--version` + `login status`. |


## Wiring a new provider

**Before merging**, read **[Subprocess environment allowlist](#subprocess-environment-allowlist)** below: if your CLI reads vendor-specific env vars, you must extend `_SAFE_SUBPROCESS_ENV_PREFIXES` in `runner.py` or the subprocess will not see them (auth and config will break silently).

1. **Adapter** — Implement `LLMCLIAdapter`: `detect()` must not raise; `build()` returns argv + optional stdin; `parse` / `explain_failure` for success and non-zero exits. Put prompt text on stdin and/or in argv as appropriate — the runner does not branch on a separate “delivery mode”; `CLIInvocation` carries what `build()` produced.
2. **Registry** — Add an entry to `CLI_PROVIDER_REGISTRY` in `registry.py` (`adapter_factory`, `model_env_key`). The dict key must match `LLM_PROVIDER` / `ProviderOption.value` / `LLMProvider` in `app/config.py`. `_create_llm_client` picks up registered CLI providers automatically (no new `elif` in `llm_client.py` for normal cases).
3. **Config** — Add the provider literal to `LLMProvider` and validators in `app/config.py` (same string as the registry key).
4. **Wizard (optional)** — If onboarding should offer the CLI: add a `ProviderOption` in `app/cli/wizard/config.py` with `credential_kind="cli"` and `adapter_factory`. `flow.py` already runs `_run_cli_llm_onboarding` for CLI providers and builds the saved-summary credential line from `provider.label` + `adapter.auth_hint`.
5. **Typing** — Prefer `adapter_factory: Callable[[], LLMCLIAdapter]` on `ProviderOption` so wizard and client stay aligned.

## Binary resolution (recommended pattern)

Use `binary_resolver.resolve_cli_binary(...)` so all adapters share the same behavior.

Resolution order:

1. Explicit binary env var (`<PROVIDER>_BIN`, e.g. Codex `CODEX_BIN`) **only if it points to a runnable file**.
2. `shutil.which(...)` lookups for platform-specific binary names.
3. Fallback install locations from `default_cli_fallback_paths(...)`.

Notes:

- Binary env vars are optional by default.
- Blank/invalid explicit paths are ignored; PATH/fallback resolution still runs.
- For Codex, keep this behavior: users can run with no `CODEX_BIN`.

## Conventions

- **No TTY**: invocation must be suitable for `subprocess.run` without an interactive session.
- **Probe vs run**: `detect()` is cheap; `CLIBackedLLMClient.invoke` probes again before exec so missing auth fails fast with a clear error.
- **Structured output**: `CLIBackedLLMClient.with_structured_output` delegates to `StructuredOutputClient` (JSON-in-prompt), same pattern as API clients.
- **Optional model envs**: use `<PROVIDER>_MODEL` (see [Per-provider env vars](#per-provider-env-vars-required-for-every-new-cli)); always optional—if unset, rely on vendor CLI defaults.

## Per-provider env vars (required for every new CLI)

**Codex is the reference.** Every subprocess LLM must expose the same *shape* of knobs:

| Env var | Role |
| ------- | ---- |
| `<PROVIDER>_BIN` | Optional explicit path to the vendor executable. Pass the same name as `explicit_env_key` to `resolve_cli_binary(...)`. Missing, blank, or invalid paths are ignored; PATH + fallbacks still run. |
| `<PROVIDER>_MODEL` | Optional model override. Register as `model_env_key` on `CLIProviderRegistration` in `registry.py`. Empty or unset → runner omits the flag and the CLI uses its default. |

**Naming:** derive `<PROVIDER>` from the registry / `LLM_PROVIDER` string: **uppercase**, then `_BIN` / `_MODEL`. Examples: `codex` → `CODEX_BIN`, `CODEX_MODEL`; a future `gemini` → `GEMINI_BIN`, `GEMINI_MODEL`.

Document both vars in the adapter module docstring or a one-line comment near `binary_env_key` / the registration entry so users and wizard copy stay aligned.

## Auth probe pattern

`detect()` must return a `CLIProbe` with `logged_in: bool | None` — three states:

| Value | Meaning | Wizard behaviour |
| ----- | ------- | ---------------- |
| `True` | Binary found **and** auth confirmed. | Proceeds immediately. |
| `False` | Binary found but definitely **not** authenticated. | Prompts user to run the login command (`auth_hint`). |
| `None` | Binary found but auth **status is unclear** (network error, unexpected output, etc.). | Asks user to retry or repick provider. |

Recommended probe sequence (mirrors Codex):

1. Run `<binary> --version` — if it fails, return `installed=False` immediately.
2. Run `<binary> <auth-status-command>` — parse stdout/stderr to classify `logged_in`.
3. Write a `_classify_<name>_auth(returncode, stdout, stderr) -> tuple[bool | None, str]`
   helper. Check **negative phrases first** (e.g. `"not logged in"` before `"logged in"`)
   to avoid substring false-positives.
4. Map network/timeout errors to `None`, not `False` — the user may be on a flaky
   connection and shouldn't be forced to re-authenticate.

See `_classify_codex_auth` in `codex.py` for a complete reference implementation.

## Subprocess environment allowlist

`CLIBackedLLMClient` in `runner.py` passes only a safe subset of env vars to the
subprocess (`_SAFE_SUBPROCESS_ENV_KEYS` + `_SAFE_SUBPROCESS_ENV_PREFIXES`).

The current prefix allowlist is `("LC_", "CODEX_")`.

**If your CLI reads custom env vars** (e.g. `CLAUDE_*`, `GEMINI_*`) you must add the
relevant prefix to `_SAFE_SUBPROCESS_ENV_PREFIXES` in `runner.py`, otherwise the
subprocess will not receive those vars and authentication or configuration will silently
fail. Add a test that asserts the required keys are forwarded.

## Codex binary resolution (reference)

Order in `CodexAdapter._resolve_binary` (now delegated to shared resolver helpers):

1. `CODEX_BIN` if set and path is runnable (explicit override).
2. `shutil.which("codex")` (and Windows `codex.cmd` / `codex.ps1`).
3. `_fallback_codex_paths()` — conventional install locations; invalid or blank `CODEX_BIN` is ignored so PATH/fallbacks still apply.

## Codex env quick reference (instance of the convention above)

All optional:

```bash
CODEX_MODEL=
CODEX_BIN=
```

- If `CODEX_MODEL` is unset, `codex exec` uses its default model behavior.
- If `CODEX_BIN` is unset, adapter resolution falls back to PATH + known install locations.

## Provider checklist (copy/paste)

- Add adapter in `app/integrations/llm_cli/`.
- Define `<PROVIDER>_BIN` + `<PROVIDER>_MODEL` per [Per-provider env vars](#per-provider-env-vars-required-for-every-new-cli); reuse `resolve_cli_binary(..., explicit_env_key=...)` for `_resolve_binary`.
- Implement `detect()` with `--version` + auth status checks; follow the three-state `logged_in` pattern above.
- Write `_classify_<name>_auth` — test against a real logged-in **and** logged-out session before merging.
- If the CLI reads custom env vars (e.g. `CLAUDE_*`), add the prefix to `_SAFE_SUBPROCESS_ENV_PREFIXES` in `runner.py`.
- Register the provider in `registry.py` and add the same `LLM_PROVIDER` value in `app/config.py`.
- (Optional) Add wizard onboarding option in `app/cli/wizard/config.py`.
- Add tests under `tests/integrations/llm_cli/` for detect/build/failure paths, including env forwarding.

## Tests

- `tests/integrations/llm_cli/` — adapter and runner unit tests; mock `subprocess` / `shutil.which` as needed.
- Platform-specific assertions must patch `app.integrations.llm_cli.binary_resolver.sys.platform` (not `codex.sys.platform`), because resolution lives in `binary_resolver.py`.
- `npm_prefix_bin_dirs` is `@lru_cache`d; tests that vary env or platform should call `npm_prefix_bin_dirs.cache_clear()` before each case (or use a shared autouse fixture) to avoid stale cache across tests.

