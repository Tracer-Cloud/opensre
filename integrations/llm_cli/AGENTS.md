# LLM CLI providers (subprocess)

Use this package when adding a new **non-interactive** LLM that shells out to a vendor CLI (like OpenAI Codex), instead of HTTP APIs.

## Layout


| File                 | Role                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `base.py`            | `LLMCLIAdapter` protocol, `CLIProbe`, `CLIInvocation`.                                      |
| `constants.py`       | Shared runner/adapter constants (probe-cache TTL, tempfail retry knobs, common timeout defaults). |
| `registry.py`        | `CLI_PROVIDER_REGISTRY`: maps `LLM_PROVIDER` → `adapter_factory` + optional `model_env_key`. `opensre doctor` uses `get_cli_provider_registration()` so any key in this registry is treated as CLI-backed—do not hardcode provider IDs in `doctor.py`. |
| `subprocess_env.py` | Filtered `env` passed to CLI subprocesses (`build_cli_subprocess_env`). Extend `_SAFE_SUBPROCESS_ENV_PREFIXES` here when a CLI needs vendor-specific env prefixes. |
| `env_overrides.py` | Optional explicit HTTP/API keys merged into `CLIInvocation.env` when `build_cli_subprocess_env` would drop them (shared tuples + `nonempty_env_values`). |
| `timeout_utils.py`   | Shared timeout env parsing (`resolve_timeout_from_env` for default + clamp behavior). |
| `probe_utils.py`     | Shared subprocess probe helpers (currently `run_version_probe` for `<binary> --version`). |
| `semver_utils.py`    | Shared semver helpers (`parse_semver_three_part`, `semver_to_tuple`). |
| `binary_resolver.py` | Shared executable resolution helpers (`env -> PATH -> fallback paths`).                     |
| `runner.py`          | `CLIBackedLLMClient`: guardrails, `detect()`, `subprocess.run`, ANSI strip, `LLMResponse`.  |
| `agent_exec.py`      | Shared machinery for the *agentic* ("hands") role used by coding-agent backends: injection-guarded task prompt, deadline-polled subprocess with pipe draining, outcome classification (limit markers). Leaf — imports nothing from `integrations/pi` or `integrations/coding_agent`. |
| `text.py`            | `flatten_messages_to_prompt` for stdin from chat-style payloads.                            |
| `codex.py`           | Reference adapter: binary resolution, `codex exec`, `--version`, and opt-in-only `login status` probing. |
| `opencode.py`        | Multi-provider CLI: `--version`, then `opencode auth list` (see `_parse_opencode_auth_list_output`). |
| `kimi.py`            | `kimi --print` path: `--version`, `kimi login status`, then env/config.toml fallback (`KIMI_API_KEY`). |
| `copilot.py`         | Piped-prompt path: `--version`, then env tokens, then `gh auth status` (and `--hostname` when `COPILOT_GH_HOST` / `GH_HOST` targets a non-default host); otherwise `logged_in=None`. Plaintext `$COPILOT_HOME/config.json` is not inspected. No OS keychain probes. |
| `pi_cli.py`          | Pi CLI (pi.dev, BYOK multi-provider) `pi -p` print mode: `--version`, then state-based auth (provider API key in env, else `~/.pi/agent/auth.json` from `/login`) — Pi has no non-interactive auth-status command. Provider keys forwarded via `CLIInvocation.env` (`PI_PROVIDER_ENV_KEYS`), never the blanket prefix. |
| `grok_cli.py`        | xAI Grok Build CLI (`grok -p --output-format plain`): `--version`, then `grok models` (~0.5 s, no LLM call) to classify auth — looks for "You are logged in" / "logged in with" in output. `XAI_API_KEY` env promotes to authenticated as a headless/CI fallback even when the probe result is unclear. `XAI_API_KEY` forwarded only via `CLIInvocation.env` (`XAI_CLI_ENV_KEYS`), never the blanket prefix. Never passes `--always-approve`. |


## Wiring a new provider

**Before merging**, read **[Subprocess environment allowlist](#subprocess-environment-allowlist)** below: if your CLI reads vendor-specific env vars, you must extend `_SAFE_SUBPROCESS_ENV_PREFIXES` in `subprocess_env.py` or the subprocess will not see them (auth and config will break silently).

Implement `LLMCLIAdapter` (`detect()` must not raise; `build()` returns argv +
optional stdin; `parse` / `explain_failure` for success and non-zero exits —
the runner does not branch on a separate "delivery mode", `CLIInvocation`
carries what `build()` produced), then follow the **[Provider checklist](#provider-checklist-copypaste)**
at the bottom of this file to wire registry, config, wizard, and tests.

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

Recommended probe sequence for CLIs with safe non-interactive auth-status commands:

1. Run `<binary> --version` — if it fails, return `installed=False` immediately.
2. Run `<binary> <auth-status-command>` — parse stdout/stderr to classify `logged_in`.
3. Write a `_classify_<name>_auth(returncode, stdout, stderr) -> tuple[bool | None, str]`
   helper. Check **negative phrases first** (e.g. `"not logged in"` before `"logged in"`)
   to avoid substring false-positives.
4. Map network/timeout errors to `None`, not `False` — the user may be on a flaky
   connection and shouldn't be forced to re-authenticate.

See `_classify_codex_auth` in `codex.py` for a complete reference implementation.

**Codex exception:** do not run `codex login status` by default. Some Codex CLI
versions can open browser OAuth while checking a session, so the Codex adapter
only runs that command when `OPENSRE_CODEX_AUTH_STATUS_PROBE=1` is explicitly
set. The normal prompt-safe status path reports `logged_in=None` and lets
`codex exec` surface request-time auth failures.

**OpenCode** is multi-provider: users may rely on `auth.json`, environment API keys, or both.
Run `opencode auth list` after `--version` and parse the reported credential/environment
counts so detection matches the CLI (do not infer auth from the JSON file alone).

**Kimi** uses `kimi login status` after `--version`, then `_check_kimi_auth_fallback()` when the
CLI did not positively confirm auth (`logged_in` not `True`): check `KIMI_API_KEY`, then API keys in
``~/.kimi/config.toml`` (or `KIMI_SHARE_DIR`). API-key-only installs may omit “logged in” phrasing in
`login status`, so the fallback mirrors real usage. The same fallback runs when `login status` times
out or fails to spawn (`logged_in=None`), so a configured API key still counts as authenticated.

## Subprocess environment allowlist

`CLIBackedLLMClient` passes only a safe subset of env vars to the subprocess via
`build_cli_subprocess_env` in `subprocess_env.py` (`_SAFE_SUBPROCESS_ENV_KEYS` +
`_SAFE_SUBPROCESS_ENV_PREFIXES`).

Shared HTTP/API overrides live in `env_overrides.py`: use `nonempty_env_values(...)` with
`OPENAI_PLATFORM_ENV_KEYS` (Codex), `HTTP_LLM_PROVIDER_ENV_KEYS` (OpenCode),
`ANTHROPIC_CLI_ENV_KEYS` (Claude Code), `CURSOR_CLI_ENV_KEYS` (Cursor Agent headless API key),
or `COPILOT_CLI_ENV_KEYS` (Copilot CLI credential envs: `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`) plus `COPILOT_CLI_CONFIG_ENV_KEYS` (`COPILOT_HOME`, `COPILOT_MODEL`, `COPILOT_GH_HOST`, `GH_HOST`).
Extend those tuples when you add a matching API-key env to `LLMSettings`.

**Kimi** does not use those tuples today: OAuth/API material is covered by forwarding any `KIMI_*` keys via `_SAFE_SUBPROCESS_ENV_PREFIXES`; `KimiAdapter.build()` uses `CLIInvocation(env=None)` and relies on that allowlist.

The current prefix allowlist includes `CODEX_`, `CURSOR_`, `CLAUDE_`, `OPENCODE_`, `KIMI_`, and locale keys (`LC_`). `COPILOT_` is deliberately NOT a prefix entry — `COPILOT_GITHUB_TOKEN` is a GitHub PAT, and a blanket prefix would leak it into every other CLI subprocess; the Copilot adapter forwards every Copilot-scoped env via its own `CLIInvocation.env` instead (see `COPILOT_CLI_ENV_KEYS` / `COPILOT_CLI_CONFIG_ENV_KEYS` in `env_overrides.py`).

**If your CLI reads custom env vars** (e.g. `GEMINI_*`) you must add the
relevant prefix to `_SAFE_SUBPROCESS_ENV_PREFIXES` in `subprocess_env.py`, otherwise the
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

- Add adapter in `integrations/llm_cli/`.
- Define `<PROVIDER>_BIN` + `<PROVIDER>_MODEL` per [Per-provider env vars](#per-provider-env-vars-required-for-every-new-cli); reuse `resolve_cli_binary(..., explicit_env_key=...)` for `_resolve_binary`.
- Implement `detect()` with `--version` + auth status checks; follow the three-state `logged_in` pattern above.
- Write `_classify_<name>_auth` — test against a real logged-in **and** logged-out session before merging.
- If the CLI reads custom env vars (e.g. `GEMINI_*`), add the prefix to `_SAFE_SUBPROCESS_ENV_PREFIXES` in `subprocess_env.py`.
- Register the provider in `registry.py` and add the same `LLM_PROVIDER` value in `config/llm_settings.py`.
- (Optional) Add wizard onboarding option in `surfaces/shared/llm_setup/catalog.py`.
- Add tests under `tests/integrations/llm_cli/` for detect/build/failure paths, including env forwarding.

## Tests

- `tests/integrations/llm_cli/` — adapter and runner unit tests; mock `subprocess` / `shutil.which` as needed.
- Platform-specific assertions must patch `integrations.llm_cli.binary_resolver.sys.platform` (not `codex.sys.platform`), because resolution lives in `binary_resolver.py`.
- `npm_prefix_bin_dirs` is `@lru_cache`d; tests that vary env or platform should call `npm_prefix_bin_dirs.cache_clear()` before each case (or use a shared autouse fixture) to avoid stale cache across tests.
