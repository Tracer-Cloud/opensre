"""Pick CLI subprocess ``env`` overrides from ``os.environ``.

``build_cli_subprocess_env`` only forwards a safe key/prefix subset from the parent process.
Vendor CLIs still need HTTP credentials sometimes; adapters merge ``nonempty_env_values(...)``
into ``CLIInvocation.env`` (same idea as Codex ``OPENAI_*``, Cursor ``CURSOR_API_KEY``, OpenCode HTTP keys).

Keep ``HTTP_LLM_PROVIDER_ENV_KEYS`` aligned with ``LLMSettings`` / ``config/llm_settings.py``
API-key env names when adding HTTP LLM providers.
"""

from __future__ import annotations

import os
from typing import Final

OPENAI_PLATFORM_ENV_KEYS: Final[tuple[str, ...]] = (
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_BASE_URL",
)

HTTP_LLM_PROVIDER_ENV_KEYS: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TRUSTEDROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_BASE_URL",
)

ANTHROPIC_CLI_ENV_KEYS: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
)

CURSOR_CLI_ENV_KEYS: Final[tuple[str, ...]] = ("CURSOR_API_KEY",)

# xAI Grok Build CLI credential envs. ``XAI_API_KEY`` is a secret and MUST NOT
# flow through the global ``_SAFE_SUBPROCESS_ENV_PREFIXES`` allowlist (a blanket
# ``XAI_`` prefix would forward the key into every other CLI subprocess). The
# Grok adapter forwards these *exclusively* via ``CLIInvocation.env`` so they
# only reach the Grok subprocess. ``XAI_BASE_URL`` supports enterprise / custom
# endpoints.
XAI_CLI_ENV_KEYS: Final[tuple[str, ...]] = (
    "XAI_API_KEY",
    "XAI_BASE_URL",
)

# Non-credential Copilot CLI config envs forwarded only via the Copilot
# adapter's ``CLIInvocation.env``. They are deliberately NOT in
# ``_SAFE_SUBPROCESS_ENV_PREFIXES``: scoping them to the Copilot subprocess
# avoids confusing other vendor CLIs with vars they do not consume.
# ``GH_HOST`` / ``COPILOT_GH_HOST`` are hostname routing for GitHub Enterprise /
# alternate GitHub endpoints (same semantics as ``gh auth status --hostname``);
# Copilot CLI must see them alongside the auth probe.
COPILOT_CLI_CONFIG_ENV_KEYS: Final[tuple[str, ...]] = (
    "COPILOT_HOME",
    "COPILOT_MODEL",
    "COPILOT_GH_HOST",
    "GH_HOST",
)

# Copilot CLI credential envs. ``COPILOT_GITHUB_TOKEN`` is a GitHub PAT and
# MUST NOT flow through the global ``_SAFE_SUBPROCESS_ENV_PREFIXES`` allowlist
# (a ``COPILOT_`` prefix entry would forward this PAT into every CLI
# subprocess — Codex, Kimi, Claude Code, etc. — which is a credential-leak
# regression). The Copilot adapter forwards these *exclusively* via
# ``CLIInvocation.env`` so they only reach the Copilot subprocess.
# ``GH_TOKEN`` / ``GITHUB_TOKEN`` are non-prefixed for the same reason.
COPILOT_CLI_ENV_KEYS: Final[tuple[str, ...]] = (
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)

# Pi CLI (pi.dev) is bring-your-own-key across ~30 providers; it reads a
# per-provider API-key env var (see https://pi.dev/docs/latest/providers).
# These are secrets, so — like the Grok/Copilot tuples above — they are
# forwarded *exclusively* via the Pi adapter's ``CLIInvocation.env`` and are
# NOT covered by the ``PI_`` entry in ``_SAFE_SUBPROCESS_ENV_PREFIXES`` (that
# prefix only carries Pi's own non-secret ``PI_*`` config vars). Keep this list
# aligned with Pi's provider catalog when it adds providers.
PI_PROVIDER_ENV_KEYS: Final[tuple[str, ...]] = (
    "ANTHROPIC_API_KEY",
    "ANT_LING_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "NVIDIA_API_KEY",
    "GEMINI_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "CLOUDFLARE_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "AI_GATEWAY_API_KEY",
    "ZAI_API_KEY",
    "ZAI_CODING_CN_API_KEY",
    "OPENCODE_API_KEY",
    "HF_TOKEN",
    "FIREWORKS_API_KEY",
    "TOGETHER_API_KEY",
    "KIMI_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "XIAOMI_API_KEY",
    "XIAOMI_TOKEN_PLAN_CN_API_KEY",
    "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
    "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
)


def nonempty_env_values(keys: tuple[str, ...]) -> dict[str, str]:
    """Return ``{name: value}`` for keys with non-empty stripped values in ``os.environ``."""
    out: dict[str, str] = {}
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            out[key] = val
    return out
