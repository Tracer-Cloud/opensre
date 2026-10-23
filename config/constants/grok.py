"""Pinned xAI subscription OAuth/inference endpoints and explicit client configuration."""

GROK_OAUTH_CLIENT_ID_ENV = "OPENSRE_GROK_OAUTH_CLIENT_ID"
GROK_ISSUER = "https://auth.x.ai"
GROK_DISCOVERY_URL = f"{GROK_ISSUER}/.well-known/openid-configuration"
GROK_RESOURCE = "https://api.x.ai/v1"
GROK_SCOPES = "openid profile email offline_access grok-cli:access api:access"
GROK_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
