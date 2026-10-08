from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from config.constants import get_store_path


def test_importing_a_leaf_does_not_load_unrelated_vendor_modules() -> None:
    """``from config.constants.product import …`` must not import every vendor leaf.

    The package used to re-export all names at import time, so any
    ``config.constants.*`` load paid for kubernetes/aws/datadog constants
    (and ``paths`` filesystem setup) before the CLI could print ``--help``.
    """
    probe = (
        "from config.constants.product import RELEASE_STAGE_BANNER; "
        "import sys; "
        "leaves = sorted("
        "n.rsplit('.', 1)[-1] for n in sys.modules "
        "if n.startswith('config.constants.') and n != 'config.constants' "
        "and n != 'config.constants.exports'"
        "); "
        "print('LEAVES', ','.join(leaves)); "
        "print('BANNER', bool(RELEASE_STAGE_BANNER))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "BANNER True" in result.stdout, result.stdout + result.stderr
    loaded = result.stdout.split("LEAVES ", 1)[1].splitlines()[0].split(",")
    assert loaded == ["product"], result.stdout


def test_package_root_still_re_exports_and_submodules() -> None:
    from config import constants

    assert constants.RELEASE_STAGE_BANNER.startswith("🚧 OpenSRE is in ")
    assert constants.billing.ORGANIZATION_ID_ENV == "ORGANIZATION_ID"
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = constants.not_a_real_constant


def test_billing_env_var_names_are_the_infra_contract() -> None:
    """Pin the credit-metering env-var names to the exact strings the org-silo
    infra injects — a rename here silently disables metering in production."""
    # Arrange / Act
    from config.constants import billing

    # Assert
    assert billing.WEBAPP_URL_ENV == "OPENSRE_WEBAPP_URL"
    assert billing.MACHINE_SECRET_ENV == "CLERK_MACHINE_SECRET_KEY"
    assert billing.USAGE_SECRET_ENV == "AGENT_USAGE_SECRET"
    assert billing.ORGANIZATION_ID_ENV == "ORGANIZATION_ID"
    assert billing.CREDITS_HTTP_TIMEOUT_SECONDS == 5.0


def test_gateway_stop_slices_fit_inside_the_process_budget() -> None:
    """Web, reload-watcher, and Slack heartbeat joins must stay smaller than
    DEFAULT_STOP_TIMEOUT_SECONDS or SIGTERM overruns the chat drain."""
    from config.constants import gateway, slack

    assert gateway.DEFAULT_STOP_TIMEOUT_SECONDS == 8.0
    assert gateway.WEB_STOP_TIMEOUT_SECONDS == 5.0
    assert gateway.SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS == 2.0
    assert slack.SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS == 2.0
    assert gateway.WEB_STOP_TIMEOUT_SECONDS < gateway.DEFAULT_STOP_TIMEOUT_SECONDS
    assert gateway.SCHEDULER_RELOAD_JOIN_TIMEOUT_SECONDS < gateway.DEFAULT_STOP_TIMEOUT_SECONDS
    assert slack.SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS < gateway.DEFAULT_STOP_TIMEOUT_SECONDS


def test_tenancy_env_var_names_are_the_infra_contract() -> None:
    """Pin the control plane's env-var names to the strings its ECS task
    definition injects — a rename here fails gateway startup in a silo."""
    # Arrange / Act
    from config.constants import tenancy

    # Assert
    assert tenancy.CREDENTIALS_API_URL_ENV == "OPENSRE_CREDENTIALS_API_URL"
    assert (
        tenancy.CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV == "OPENSRE_CREDENTIALS_BOOTSTRAP_SECRET_ARN"
    )


def test_the_organization_id_is_re_exported() -> None:
    """Callers may import it from the package root."""
    # Arrange
    from config import constants

    # Act / Assert
    assert constants.ORGANIZATION_ID_ENV == "ORGANIZATION_ID"
    for name in (
        "ORGANIZATION_ID_ENV",
        "CREDENTIALS_API_URL_ENV",
        "CREDENTIALS_BOOTSTRAP_SECRET_ARN_ENV",
    ):
        assert name in constants.__all__


def test_mcp_gateway_capability_constants_are_re_exported() -> None:
    """The constants facade exposes MCP capability names to every layer."""
    from config import constants

    assert constants.MCP_GATEWAY_MUTATION_CAPABILITY == "mcp_gateway_mutation"
    assert constants.MCP_GATEWAY_MUTATION_TOOL == "call_mcp_gateway_tool"
    assert "MCP_GATEWAY_MUTATION_CAPABILITY" in constants.__all__
    assert "MCP_GATEWAY_MUTATION_TOOL" in constants.__all__


def test_repl_sound_constants_are_re_exported() -> None:
    """Callers may import the shell-sound names from the package root."""
    from config import constants

    assert constants.SOUND_NOTIFICATIONS_ENV == "OPENSRE_SOUND"
    assert constants.SOUND_MIN_TURN_SECONDS == 8.0
    assert "SOUND_NOTIFICATIONS_ENV" in constants.__all__
    assert "SOUND_MIN_TURN_SECONDS" in constants.__all__


def test_remote_sync_endpoint_url_env_is_re_exported() -> None:
    """Verify REMOTE_SYNC_ENDPOINT_URL_ENV is re-exported from config.constants."""
    # Arrange / Act
    from config import constants

    # Assert
    assert constants.REMOTE_SYNC_ENDPOINT_URL_ENV == "OPENSRE_REMOTE_SYNC_ENDPOINT_URL"
    assert "REMOTE_SYNC_ENDPOINT_URL_ENV" in constants.__all__


def test_split_config_constants_are_re_exported() -> None:
    """The constants facade remains the canonical import path after the split."""
    from config import constants

    expected = {
        "CLERK_ISSUER_ENV": "CLERK_ISSUER",
        "CLERK_JWKS_URL_ENV": "CLERK_JWKS_URL",
        "DEPLOYMENT_ENV_ENV": "ENV",
        "TRACER_BASE_URL_DEV": "https://staging.tracer.cloud",
        "TRACER_BASE_URL_PROD": "https://app.tracer.cloud",
    }

    for name, value in expected.items():
        assert getattr(constants, name) == value
        assert name in constants.__all__


@pytest.mark.parametrize("module", ["billing", "tenancy"])
def test_constants_module_stays_a_leaf(module: str) -> None:
    """``config`` sits at the bottom layer, so the constants must not
    reach up into another package — that would form an import cycle."""
    # Arrange / Act
    from pathlib import Path as _Path

    source = _Path(f"config/constants/{module}.py").read_text(encoding="utf-8")

    # Assert: no upward import of a sibling top-level package.
    for package in ("integrations", "gateway", "core", "infrastructure", "tools", "surfaces"):
        assert f"import {package}" not in source
        assert f"from {package}" not in source


def test_llm_env_var_names_are_the_infra_contract() -> None:
    """Pin the Azure OpenAI connection env-var names to their exact strings."""
    # Arrange / Act
    from config.constants import llm

    # Assert
    assert llm.AZURE_OPENAI_BASE_URL_ENV == "AZURE_OPENAI_BASE_URL"
    assert llm.AZURE_OPENAI_API_VERSION_ENV == "AZURE_OPENAI_API_VERSION"
    assert llm.AZURE_OPENAI_API_KEY_ENV == "AZURE_OPENAI_API_KEY"


def test_provider_catalog_and_wizard_share_the_same_azure_constants() -> None:
    """The Azure spec + wizard option must reference the one set of constants,
    not re-typed literals, so they cannot drift apart."""
    # Arrange
    from config.constants import llm
    from config.llm_auth.provider_catalog import require_provider_spec
    from surfaces.shared.llm_setup.catalog import SUPPORTED_PROVIDERS

    spec = require_provider_spec("azure-openai")
    (option,) = [opt for opt in SUPPORTED_PROVIDERS if opt.value == "azure-openai"]

    # Act / Assert: both sides equal the centralized constants.
    assert spec.api_key_env == option.api_key_env == llm.AZURE_OPENAI_API_KEY_ENV
    assert spec.endpoint_env == option.endpoint_env == llm.AZURE_OPENAI_BASE_URL_ENV
    assert spec.api_version_env == option.api_version_env == llm.AZURE_OPENAI_API_VERSION_ENV


def test_custom_gateway_env_var_names_are_the_infra_contract() -> None:
    """Pin the custom OpenAI-/Anthropic-compatible gateway env-var names."""
    from config.constants import llm

    assert llm.CUSTOM_OPENAI_BASE_URL_ENV == "CUSTOM_OPENAI_BASE_URL"
    assert llm.CUSTOM_OPENAI_API_KEY_ENV == "CUSTOM_OPENAI_API_KEY"
    assert llm.CUSTOM_OPENAI_MODEL_ENV == "CUSTOM_OPENAI_MODEL"
    assert llm.CUSTOM_OPENAI_REASONING_MODEL_ENV == "CUSTOM_OPENAI_REASONING_MODEL"
    assert llm.CUSTOM_OPENAI_CLASSIFICATION_MODEL_ENV == "CUSTOM_OPENAI_CLASSIFICATION_MODEL"
    assert llm.CUSTOM_OPENAI_TOOLCALL_MODEL_ENV == "CUSTOM_OPENAI_TOOLCALL_MODEL"
    assert llm.CUSTOM_ANTHROPIC_BASE_URL_ENV == "CUSTOM_ANTHROPIC_BASE_URL"
    assert llm.CUSTOM_ANTHROPIC_API_KEY_ENV == "CUSTOM_ANTHROPIC_API_KEY"
    assert llm.CUSTOM_ANTHROPIC_MODEL_ENV == "CUSTOM_ANTHROPIC_MODEL"
    assert llm.CUSTOM_ANTHROPIC_REASONING_MODEL_ENV == "CUSTOM_ANTHROPIC_REASONING_MODEL"
    assert llm.CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL_ENV == "CUSTOM_ANTHROPIC_CLASSIFICATION_MODEL"
    assert llm.CUSTOM_ANTHROPIC_TOOLCALL_MODEL_ENV == "CUSTOM_ANTHROPIC_TOOLCALL_MODEL"


@pytest.mark.parametrize(
    ("slug", "api_key_env", "base_url_env", "reasoning_env"),
    [
        (
            "custom-openai",
            "CUSTOM_OPENAI_API_KEY",
            "CUSTOM_OPENAI_BASE_URL",
            "CUSTOM_OPENAI_REASONING_MODEL",
        ),
        (
            "custom-anthropic",
            "CUSTOM_ANTHROPIC_API_KEY",
            "CUSTOM_ANTHROPIC_BASE_URL",
            "CUSTOM_ANTHROPIC_REASONING_MODEL",
        ),
    ],
)
def test_provider_catalog_and_wizard_share_the_same_custom_constants(
    slug: str, api_key_env: str, base_url_env: str, reasoning_env: str
) -> None:
    """The custom spec + wizard option must reference one set of env names, so the
    onboarding catalog and the runtime cannot drift apart for either gateway."""
    from config.llm_auth.provider_catalog import require_provider_spec
    from surfaces.shared.llm_setup.catalog import SUPPORTED_PROVIDERS

    spec = require_provider_spec(slug)
    (option,) = [opt for opt in SUPPORTED_PROVIDERS if opt.value == slug]

    assert spec.api_key_env == option.api_key_env == api_key_env
    assert spec.endpoint_env == option.endpoint_env == base_url_env
    assert option.model_env == reasoning_env


def test_get_store_path_honors_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom-dir" / "opensre.json"
    monkeypatch.setenv("OPENSRE_WIZARD_STORE_PATH", str(override))

    assert get_store_path() == override


def test_get_store_path_defaults_away_from_real_home_during_tests(tmp_path: Path) -> None:
    """Regression guard for #3721.

    The root ``tests/conftest.py`` autouse fixture ``_isolate_opensre_home_files``
    sets ``OPENSRE_WIZARD_STORE_PATH`` for every test by default, specifically so
    a test that forgets to monkeypatch ``get_store_path`` can never fall through
    to the developer's real ``~/.opensre/opensre.json``. This test intentionally
    does *not* patch anything itself, to prove that default is in effect.
    """
    resolved = get_store_path()

    assert resolved != Path.home() / ".opensre" / "opensre.json"
    # Same tmp_path instance the autouse fixture pointed OPENSRE_WIZARD_STORE_PATH at.
    assert resolved == tmp_path / "opensre.json"
