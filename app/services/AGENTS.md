# LLM API providers (hosted HTTP)

Use this directory when adding or updating an **API-backed** LLM provider (Anthropic,
OpenAI-compatible, Bedrock, etc.). For subprocess CLIs, use
`app/integrations/llm_cli/AGENTS.md`.

Primary reference for provider discovery:

- [https://docs.openclaw.ai/providers](https://docs.openclaw.ai/providers)

## Where provider wiring lives


| File                         | Role                                                                              |
| ---------------------------- | --------------------------------------------------------------------------------- |
| `app/config.py`              | Declares `LLMProvider`, provider env vars, defaults, and validation requirements. |
| `app/services/llm_client.py` | Routes `LLM_PROVIDER` to the runtime client implementation.                       |
| `app/cli/wizard/config.py`   | Defines onboarding metadata (`SUPPORTED_PROVIDERS`) and model choices.            |
| `app/cli/wizard/env_sync.py` | Keeps `.env` values in sync when provider/model changes.                          |


## Adding a new API provider

1. Add provider literal to `LLMProvider` and normalization/validation paths in `app/config.py`.
2. Add provider metadata in `app/cli/wizard/config.py` (`ProviderOption`, model env vars, defaults).
3. Add runtime routing in `app/services/llm_client.py`:
  - OpenAI-compatible providers should use `OpenAILLMClient` with provider base URL + key env var.
  - Provider-specific SDKs can use a dedicated client class if needed.
4. Update `.env` sync behavior if you introduce new model/API env keys.
5. Add or update tests under `tests/services/` (and wizard tests if onboarding changes) for the new provider path.

## Conventions

- Keep provider keys canonical: lowercase provider name in `LLM_PROVIDER`.
- Use one source of truth for provider defaults in `app/config.py`.
- Treat API keys as secrets: store in keychain via existing credential helpers, not in plaintext docs.
- Prefer OpenAI-compatible client path for providers exposing compatible APIs.

