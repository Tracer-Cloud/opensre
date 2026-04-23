# LLM CLI providers (subprocess)

Use this package when adding a new **non-interactive** LLM that shells out to a vendor CLI (like OpenAI Codex), instead of HTTP APIs.

## Layout


| File                 | Role                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `base.py`            | `LLMCLIAdapter` protocol, `CLIProbe`, `CLIInvocation`, `PromptDelivery`.                    |
| `binary_resolver.py` | Shared executable resolution helpers (`env -> PATH -> fallback paths`).                     |
| `runner.py`          | `CLIBackedLLMClient`: guardrails, `detect()`, `subprocess.run`, ANSI strip, `LLMResponse`.  |
| `text.py`            | `flatten_messages_to_prompt` for stdin from chat-style payloads.                            |
| `codex.py`           | Reference adapter: binary resolution, `codex exec`, probe via `--version` + `login status`. |


## Wiring a new provider

1. **Adapter** — Implement `LLMCLIAdapter`: `detect()` must not raise; `build()` returns argv + optional stdin; `parse` / `explain_failure` for success and non-zero exits.
2. **Factory** — In `app/services/llm_client.py`, extend `_create_llm_client` with a branch for your `LLM_PROVIDER` value; return `CLIBackedLLMClient(YourAdapter(), ...)`. Add provider to `LLMProvider` / `LLMSettings` in `app/config.py` if needed.
3. **Wizard (optional)** — If onboarding should offer the CLI: add a `ProviderOption` in `app/cli/wizard/config.py` with `credential_kind="cli"` and `adapter_factory`; branch in `app/cli/wizard/flow.py` already runs `_run_cli_llm_onboarding` for CLI providers.
4. **Typing** — Prefer `adapter_factory: Callable[[], LLMCLIAdapter]` on `ProviderOption` so wizard and client stay aligned.

## Binary resolution (recommended pattern)

Use `binary_resolver.resolve_cli_binary(...)` so all adapters share the same behavior.

Resolution order:

1. Explicit binary env var (for Codex: `CODEX_BIN`) **only if it points to a runnable file**.
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
- **Optional model envs**: provider model env vars (for Codex: `CODEX_MODEL`) should be optional; if unset, rely on vendor CLI defaults.

## Codex binary resolution (reference)

Order in `CodexAdapter._resolve_binary` (now delegated to shared resolver helpers):

1. `CODEX_BIN` if set and path is runnable (explicit override).
2. `shutil.which("codex")` (and Windows `codex.cmd` / `codex.ps1`).
3. `_fallback_codex_paths()` — conventional install locations; invalid or blank `CODEX_BIN` is ignored so PATH/fallbacks still apply.

## Codex env quick reference

All optional:

```bash
CODEX_MODEL=
CODEX_BIN=
```

- If `CODEX_MODEL` is unset, `codex exec` uses its default model behavior.
- If `CODEX_BIN` is unset, adapter resolution falls back to PATH + known install locations.

## Provider checklist (copy/paste)

- Add adapter in `app/integrations/llm_cli/`.
- Reuse `resolve_cli_binary(...)` for `_resolve_binary`.
- Implement `detect()` with `--version` + auth status checks.
- Wire provider in `app/services/llm_client.py` and `app/config.py`.
- (Optional) Add wizard onboarding option in `app/cli/wizard/config.py`.
- Add tests under `tests/integrations/llm_cli/` for detect/build/failure paths.

## Tests

- `tests/integrations/llm_cli/` — adapter and runner unit tests; mock `subprocess` / `shutil.which` as needed.

