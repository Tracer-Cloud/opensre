"""Pinned xAI subscription OAuth/inference endpoints and public client defaults."""

GROK_OAUTH_CLIENT_ID_ENV = "OPENSRE_GROK_OAUTH_CLIENT_ID"
# Public Grok CLI registration shared by OpenCode, Kilo, and Hermes:
# https://github.com/Kilo-Org/kilocode/blob/main/packages/opencode/src/plugin/xai.ts
GROK_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
GROK_OAUTH_REFERRER = "opensre"
GROK_ISSUER = "https://auth.x.ai"
GROK_DISCOVERY_URL = f"{GROK_ISSUER}/.well-known/openid-configuration"
GROK_RESOURCE = "https://api.x.ai/v1"
GROK_SCOPES = "openid profile email offline_access grok-cli:access api:access"
GROK_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
