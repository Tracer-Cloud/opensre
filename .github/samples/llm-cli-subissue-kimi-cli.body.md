## Parent epic

Part of **Tracer-Cloud/opensre#642**. Link this issue to the parent in the Development sidebar or issue description.

## Goal

Integrate **[Kimi Code CLI](https://github.com/MoonshotAI/kimi-cli)** (MoonshotAI, Apache-2.0) as an `LLM_PROVIDER` via the shared **non-interactive** CLI path (`LLMCLIAdapter` + `CLIBackedLLMClient`), analogous to **OpenAI Codex** (`codex exec`-style: one-shot invocation, no REPL, no approval prompts, suitable for `subprocess.run` without a TTY).

## Non-interactive requirement (must-have)

- **Same contract as Codex CLI:** the integration must use a **single, scriptable** CLI invocation that accepts the flattened prompt (stdin and/or argv from `build()`), runs to completion, and exits with a clear status code—**not** an interactive session, wizard, or tool that blocks on user input.
- **Spike first:** Kimi Code CLI is agent- and TUI-oriented; confirm a **supported** non-interactive / one-shot mode (documented or via `kimi --help`) before a full adapter. If no headless contract exists, keep this issue spike-only and stop.

## Vendor documentation (required)

Kimi Code CLI documentation hub: https://moonshotai.github.io/kimi-cli/en/

## Scope

- [ ] **Spike (if needed):** confirm the exact subcommand(s) and flags for the non-interactive path; document binary name(s), auth mechanism, and how to probe “logged in vs not vs unknown”.
- [ ] **Adapter** in `app/integrations/llm_cli/` implementing `LLMCLIAdapter` (`detect`, `build`, `parse`, `explain_failure`).
- [ ] **Registry** entry in `registry.py` (`adapter_factory`, `model_env_key`) + **`LLMProvider` / validators** in `app/config.py` (same string as registry key).
- [ ] **Subprocess env:** extend `_SAFE_SUBPROCESS_ENV_PREFIXES` in `runner.py` when the CLI relies on vendor env vars; add tests that required keys are forwarded.
- [ ] **Wizard (optional):** `ProviderOption` with `credential_kind="cli"` and `adapter_factory` in `app/cli/wizard/config.py`.
- [ ] **Tests** under `tests/integrations/llm_cli/` (detect/build/failure; mock `subprocess` / `shutil.which` as appropriate).
- [ ] **User path (Quick Start):** **`opensre onboard`** then **`opensre investigate`** works with this `LLM_PROVIDER`, matching [Tracer-Cloud/opensre#quick-start](https://github.com/Tracer-Cloud/opensre#quick-start) (README sample alert/fixture or a documented equivalent).
- [ ] **Cross-OS (Windows, Linux, macOS):** binary resolution, env forwarding, and non-interactive invocation behave correctly on each OS the vendor CLI supports; extend tests for platform-specific branches (e.g. `*.exe`, path rules) like other CLI adapters. If the vendor ships only some OSes, state that explicitly in the PR.

## Acceptance criteria

1. **`detect()`** never raises; returns `CLIProbe` with **`logged_in` in `{True, False, None}`** per the three-state pattern in `app/integrations/llm_cli/AGENTS.md` (auth confirmed / not authenticated / unclear).
2. **`build()`** produces **`CLIInvocation`** that the runner can execute with **`subprocess.run`** (no interactive/TUI assumptions); timeout and cwd are sensible and documented.
3. **`parse` / `explain_failure`** handle success and failure without leaking secrets.
4. **Binary resolution** uses **`resolve_cli_binary`** with an explicit `*_BIN`-style override env + PATH + fallbacks, consistent with other CLI adapters.
5. **`LLM_PROVIDER=...`** selects the new client via **`CLI_PROVIDER_REGISTRY`**; optional model env mirrors **`CODEX_MODEL`** semantics (empty/unset → omit override; CLI default applies).
6. **CI:** repository quality gates pass (`make lint`, `make typecheck`, `make test-cov` or the usual PR checks).
7. **End-to-end demo:** Share a recording, GIF, or numbered steps that follow [Quick Start](https://github.com/Tracer-Cloud/opensre#quick-start): **`opensre onboard`** (this provider selected/configured), then **`opensre investigate`** with the README sample or an equivalent fixture, through a completed investigation.
8. **Cross-OS:** Verify **Windows**, **Linux**, and **macOS** for the onboarding + investigate path (or document vendor/OS limits and how we degrade). PR should mention what was exercised on each OS or point to CI matrix coverage.

## Implementation reference

- **This repo:** `app/integrations/llm_cli/AGENTS.md` and `app/integrations/llm_cli/codex.py` (reference implementation).
- **Non-interactive exec mental model (one link):** [OpenAI Codex CLI](https://github.com/openai/codex) — one-shot / non-TTY usage comparable to our `codex exec` adapter; vendor flags will differ.

## Out of scope

- Interactive-only CLIs with no supported non-interactive mode.
- Long-running agent UIs, approval loops, or integrations that do not map to a single `invoke` call.
