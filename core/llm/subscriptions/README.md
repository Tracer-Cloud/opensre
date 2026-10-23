# Subscription adapters

`config/llm_auth/subscriptions.py` owns provider identity, model defaults, authentication
method, and setup hints. Membership in this catalog selects subscription billing;
`auth_method` describes how the user authorizes it (`oauth`, `api_key`, or a
provider-supported alternative). An OAuth login does not itself establish subscription
entitlement, and subscription billing does not require a browser.

`registry.py` pairs two contracts defined in `types.py`:

- `SubscriptionAdapter`: login, request-time credential resolution/renewal, model
  discovery, verification, and logout. Login selects a model before activating an
  account. Resolution never opens a browser or reads an ordinary API-billing key.
- `SubscriptionClientFactory`: agent and reasoning client construction. A provider
  owns its endpoint, request format, tool serialization, and subscription restrictions.
  Using the same API protocol does not establish subscription eligibility.

Setup dispatches by provider identity. Runtime construction, readiness, and cache keys
use the existing provider catalog; a catalog-only entry fails without an adapter.
Clients resolve the active account on each request. Tokens never enter cache keys.

## Adding a provider

1. Confirm the supported onboarding/client registration, eligible plans/workloads,
   subscription endpoint, scopes, discovery, renewal/revocation, and quota semantics.
2. Add metadata to the existing subscription catalog and the provider literal to
   `config/llm_settings.py`. Keep usage-billed API connections separate.
3. Implement both contracts in provider-owned modules and add one registration.
   Reuse `subscription_lock`, `read_store`, and `write_store` for protected atomic
   storage. Isolate provider/account credentials, serialize rotating refresh tokens,
   clear terminally invalid grants, preserve credentials on transient failures, and
   retain registrations during logout.
4. Implement provider-specific inference. Providers using Responses may reuse
   `ResponsesAgentClient`/`ResponsesLLMClient`, supplying their own request serializer.
   ChatGPT's `additional_tools` extension must not be sent to other providers.
5. Cover account/provider isolation, explicit billing, request-time renewal, model
   selection, terminal/transient errors, tool/result and reasoning replay, live text,
   interrupted streams, cancellation/closure, usage accounting, and quota failures.
   A failure must never switch to an API-billed credential or route.
6. Document setup and perform live login, inference/tool replay, renewal, reconnect,
   quota, billing, and logout acceptance with an eligible account.

## Grok status

The Grok adapter uses RFC 8628 device authorization and LiteLLM's native xAI Responses
route. The browser can run on another device; there is no loopback callback.
The default is the public Grok CLI client registration shared by OpenCode, Kilo,
and Hermes, with `referrer=opensre` attribution. See
[Kilo's implementation](https://github.com/Kilo-Org/kilocode/blob/main/packages/opencode/src/plugin/xai.ts).
`OPENSRE_GROK_OAUTH_CLIENT_ID` optionally overrides it for new accounts. Credentials
retain their original client ID across refresh and reconnect; no other application's
credential store is reused.

xAI officially announced subscription access for
[OpenCode](https://x.ai/news/grok-opencode) and
[Kilo Code](https://x.ai/news/grok-kilocode). This establishes those integrations;
current plan eligibility in OpenSRE and live subscription billing remain
unconfirmed. Authentication/model-list success does not prove inference entitlement.
The adapter is covered with synthetic OAuth and real LiteLLM HTTP/SSE tests; local
subscription acceptance is still required before claiming production support.
