# Hosted LLM Runtime

This package owns hosted LLM provider clients and runtime helpers used by the
agent loop. Subprocess-backed LLM CLIs live under `integrations/llm_cli/`.

## Where provider wiring lives

| File | Role |
| --- | --- |
| `config/llm_models.py` | Canonical per-provider model-tier defaults, base URLs, and `*_LLM_CONFIG` shapes. |
| `config/llm_settings.py` | Declares `LLMProvider`, `LLMSettings` validation, and env-backed resolution. |
| `config/llm_auth/provider_catalog.py` | Canonical `ProviderSpec` metadata shared by wizard, auth, and runtime checks. |
| `core/llm/factory.py` | Single routing entrypoint: `resolve_llm_route()`, `get_llm(role)`, `reset_llm_clients()`. |
| `core/llm/client_builders.py` | Construct the client for a resolved route: `build_agent_client()`, `build_reasoning_client()`. |
| `core/llm/providers/provider_registry.py` | `FIRST_PARTY_PROVIDERS` table (models, max_tokens, LiteLLM prefix, api-key env) the builders read. |
| `core/llm/transport_mode.py` | `OPENSRE_LLM_TRANSPORT` (`sdk` vs `litellm`) and `use_litellm_for_provider()`. |
| `core/llm/internal/client_cache_key.py` | Singleton cache invalidation key `(transport, runtime_provider)`. |
| `core/llm/providers/openai_compat_providers.py` | OpenAI-compatible provider catalog and model/base-URL resolution. |
| `core/llm/providers/azure_openai.py` | Azure OpenAI helpers: endpoint normalization, deployment selection, LiteLLM kwargs. |
| `core/llm/providers/vertex_ai.py` | Vertex AI helpers: project/location resolution, LiteLLM kwargs (ambient ADC auth, no API key). |
| `core/llm/transports/litellm/routing.py` | Per-provider LiteLLM client construction (model prefix, `api_base`, `api_version`). |
| `core/llm/transports/litellm/clients.py` | `LiteLLMAgentClient` / `LiteLLMLLMClient` wrappers around `litellm.completion`. |
| `core/llm/transports/sdk/agent_clients.py` | Native SDK tool-calling clients (Anthropic, OpenAI, Bedrock, CLI-backed). |
| `core/llm/transports/sdk/llm_clients.py` | Native SDK non-agent clients. |
| `core/llm/shared/tool_schema_normalize.py` | JSON Schema normalization shared by strict tool-calling adapters. |
| `surfaces/shared/llm_setup/catalog.py` | Onboarding metadata (`SUPPORTED_PROVIDERS`) and model choices. |
| `surfaces/shared/llm_setup/env_sync.py` | `.env` synchronization when provider/model choices change. |

User-facing setup and env var tables: [`docs/platform/llm-providers.mdx`](../../docs/platform/llm-providers.mdx).

## Transport: native SDK vs LiteLLM

Default path is **native vendor SDKs** (`OPENSRE_LLM_TRANSPORT` unset or `sdk`).

**LiteLLM path** (`OPENSRE_LLM_TRANSPORT=litellm`): routes hosted API providers through
`core/llm/transports/litellm/routing.py` instead of `core/llm/transports/sdk/*`.

**Azure OpenAI** (`LLM_PROVIDER=azure-openai`) **always** uses LiteLLM — even when
`OPENSRE_LLM_TRANSPORT` is unset. Onboarding writes `OPENSRE_LLM_TRANSPORT=litellm` to
`.env`; switching away from Azure removes that key so other providers return to SDK routing.

Dispatch entrypoints — all routing lives in **one** place, `core/llm/factory.py`;
construction lives in `core/llm/client_builders.py`:

```text
get_llm(role)  # role ∈ {AGENT, REASONING, CLASSIFICATION, TOOLCALL}   # factory.py
  → resolve_llm_route()               # the single provider/transport decision  # factory.py
  → client_builders.build_agent_client(route) / build_reasoning_client(route, model_type)
      cli_provider_registration?  → CLI-backed subprocess client
      use_litellm_for_provider? → build_litellm_*_client(settings, provider)   # transports/litellm/routing.py
      else      → native SDK client in transports/sdk/agent_clients.py or transports/sdk/llm_clients.py
```

When changing routing, edit only `resolve_llm_route` in `factory.py`; when changing how a
provider's client is built, edit the builders in `client_builders.py` — there is no second
copy to keep in sync.

One cache in `factory.py` keyed by `(role, transport, runtime_provider)`, invalidated
together on `(transport, runtime_provider)` change (not transport alone). REPL `/model`
and wizard env sync call `reset_llm_clients()` directly.

## Adding a Hosted API Provider

1. Add the provider literal to `LLMProvider` and normalization/validation paths in `config/llm_settings.py`.
2. Add a `ProviderModelDefaults` row in `config/llm_models.py` (model-tier defaults / base URL).
3. Add `ProviderSpec` in `config/llm_auth/provider_catalog.py` and matching `ProviderOption` in
   `surfaces/shared/llm_setup/catalog.py` (model env vars, defaults, `endpoint_env` if needed).
3. Add runtime construction (routing itself stays in `core/llm/factory.py`; clients are built in `core/llm/client_builders.py`):
   - **First-party provider** (its own SDK models + a LiteLLM prefix): add **one row** to
     `FIRST_PARTY_PROVIDERS` in `core/llm/providers/provider_registry.py` — the SDK and LiteLLM
     builders read the table, so no per-provider branch is needed unless the client class is new.
   - **SDK path:** add the client class in `core/llm/transports/sdk/llm_clients.py` and/or
     `core/llm/transports/sdk/agent_clients.py`; the builders in `client_builders.py` select it.
   - **LiteLLM path (optional or required):** covered by the registry row; only add a branch in
     `core/llm/transports/litellm/routing.py` for a non-standard case (e.g. Azure).
   - **OpenAI-compatible:** register in `providers/openai_compat_providers.py` (SDK compat path) and/or
     `transports/litellm/routing.py` (LiteLLM path).
4. Update `surfaces/shared/llm_setup/env_sync.py` if you introduce new non-secret env keys; keep endpoint
   keys in `active_non_secret` when the provider needs persisted URL/version settings.
5. Add or update tests under `tests/core/runtime/llm/` and wizard tests if onboarding changes.

### Azure OpenAI (`azure-openai`)

Azure uses **deployment names** (not public OpenAI model IDs) and a resource **base URL**:

- `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION` (default applied when unset)
- `AZURE_OPENAI_*_MODEL` env vars hold deployment names in the user's Azure resource
- LiteLLM model string: `azure/<deployment>` via `azure_openai_litellm_model()`

Do not add a separate Azure client class — extend `transports/litellm/routing.py` and helpers in
`providers/azure_openai.py`.

### Google Vertex AI (`vertex-ai`)

Vertex AI authenticates via Google Application Default Credentials (ADC) — no API key:

- `VERTEX_AI_PROJECT`, `VERTEX_AI_LOCATION` (defaults to `us-central1`)
- `VERTEX_AI_*_MODEL` env vars hold Gemini model IDs served via Vertex (e.g. `gemini-2.5-pro`)
- LiteLLM model string: `vertex_ai/<model>` via `resolve_vertex_ai_request_kwargs()`

Do not add a separate Vertex client class — extend `transports/litellm/routing.py` and helpers in
`providers/vertex_ai.py`. Like Bedrock, `credential_kind="ambient"`: OpenSRE never stores a
Vertex secret and does not enforce a required-field precondition — a missing project surfaces
as a LiteLLM/google-auth error at request time.
