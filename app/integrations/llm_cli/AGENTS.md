# LLM CLI providers (subprocess)

Use this package when adding a new **non-interactive** LLM that shells out to a vendor CLI (like OpenAI Codex), instead of HTTP APIs.

## Layout

| File | Role |
| --- | --- |
| `base.py` | `LLMCLIAdapter` protocol, `CLIProbe`, `CLIInvocation`, `PromptDelivery`. |
| `runner.py` | `CLIBackedLLMClient`: guardrails, `detect()`, `subprocess.run`, ANSI strip, `LLMResponse`. |
| `text.py` | `flatten_messages_to_prompt` for stdin from chat-style payloads. |
| `codex.py` | Reference adapter: binary resolution, `codex exec`, probe via `--version` + `login status`. |

## Wiring a new provider

1. **Adapter** — Implement `LLMCLIAdapter`: `detect()` must not raise; `build()` returns argv + optional stdin; `parse` / `explain_failure` for success and non-zero exits.
2. **Factory** — In `app/services/llm_client.py`, extend `_create_llm_client` with a branch for your `LLM_PROVIDER` value; return `CLIBackedLLMClient(YourAdapter(), ...)`. Add provider to `LLMProvider` / `LLMSettings` in `app/config.py` if needed.
3. **Wizard (optional)** — If onboarding should offer the CLI: add a `ProviderOption` in `app/cli/wizard/config.py` with `credential_kind="cli"` and `adapter_factory`; branch in `app/cli/wizard/flow.py` already runs `_run_cli_llm_onboarding` for CLI providers.
4. **Typing** — Prefer `adapter_factory: Callable[[], LLMCLIAdapter]` on `ProviderOption` so wizard and client stay aligned.

## Conventions

- **No TTY**: invocation must be suitable for `subprocess.run` without an interactive session.
- **Probe vs run**: `detect()` is cheap; `CLIBackedLLMClient.invoke` probes again before exec so missing auth fails fast with a clear error.
- **Structured output**: `CLIBackedLLMClient.with_structured_output` delegates to `StructuredOutputClient` (JSON-in-prompt), same pattern as API clients.

## Codex binary resolution (reference)

Order in `CodexAdapter._resolve_binary`:

1. `CODEX_BIN` if set and path is an existing file (explicit override).
2. `shutil.which("codex")` (and Windows `codex.cmd` / `codex.ps1`).
3. `_fallback_codex_paths()` — conventional install locations; invalid or blank `CODEX_BIN` is ignored so PATH/fallbacks still apply.

## Tests

- `tests/integrations/llm_cli/` — adapter and runner unit tests; mock `subprocess` / `shutil.which` as needed.
