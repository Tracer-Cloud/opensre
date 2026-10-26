"""Tests for the Pi CLI adapter (``pi -p`` non-interactive print mode).

Unit tests mock the version probe, ``shutil.which``, and the credential file so
they run fully offline. The final ``live_llm`` test exercises a real ``pi``
binary against a real Gemini model and self-skips unless both are available.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations.llm_cli.pi_cli import PiAdapter

_PROBE = "integrations.llm_cli.pi_cli.run_version_probe"
_WHICH = "integrations.llm_cli.binary_resolver.shutil.which"
_PI_FALLBACK_PATHS = "integrations.llm_cli.pi_cli._fallback_pi_paths"


# --------------------------------------------------------------------------- #
# detect()
# --------------------------------------------------------------------------- #
@patch(_PROBE, return_value=("pi 0.5.0", None))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_detect_logged_in_via_provider_env_key(
    _mock_which: MagicMock, _mock_probe: MagicMock
) -> None:
    with patch.dict(os.environ, {"GEMINI_API_KEY": "  test-key  "}, clear=True):
        probe = PiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert probe.bin_path == "/usr/bin/pi"
    assert probe.version == "0.5.0"
    assert "GEMINI_API_KEY" in probe.detail


@patch(_PROBE, return_value=("pi 0.5.0", None))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_detect_logged_in_via_auth_json(
    _mock_which: MagicMock, _mock_probe: MagicMock, tmp_path: Path
) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "auth.json").write_text(
        '{"anthropic": {"type": "oauth", "access": "tok"}}', encoding="utf-8"
    )

    with patch.dict(os.environ, {"PI_AGENT_DIR": str(agent_dir)}, clear=True):
        probe = PiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is True
    assert "auth.json" in probe.detail


@patch(_PROBE, return_value=("pi 0.5.0", None))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_detect_not_logged_in_when_no_key_and_no_auth_file(
    _mock_which: MagicMock, _mock_probe: MagicMock, tmp_path: Path
) -> None:
    with patch.dict(os.environ, {"PI_AGENT_DIR": str(tmp_path)}, clear=True):
        probe = PiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False
    assert "/login" in probe.detail or "API key" in probe.detail


@patch(_PROBE, return_value=("pi 0.5.0", None))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_detect_unreadable_auth_json_returns_none(
    _mock_which: MagicMock, _mock_probe: MagicMock, tmp_path: Path
) -> None:
    (tmp_path / "auth.json").write_text("not-json{", encoding="utf-8")

    with patch.dict(os.environ, {"PI_AGENT_DIR": str(tmp_path)}, clear=True):
        probe = PiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is None  # unclear, invocation will verify


@patch(_PROBE, return_value=("pi 0.5.0", None))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_detect_empty_auth_json_not_logged_in(
    _mock_which: MagicMock, _mock_probe: MagicMock, tmp_path: Path
) -> None:
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")

    with patch.dict(os.environ, {"PI_AGENT_DIR": str(tmp_path)}, clear=True):
        probe = PiAdapter().detect()

    assert probe.installed is True
    assert probe.logged_in is False


@patch(_PROBE, return_value=(None, "`/usr/bin/pi --version` failed: boom"))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_detect_version_probe_failure_marks_not_installed(
    _mock_which: MagicMock, _mock_probe: MagicMock
) -> None:
    probe = PiAdapter().detect()
    assert probe.installed is False
    assert probe.logged_in is None
    assert "boom" in probe.detail


@patch(_PI_FALLBACK_PATHS, return_value=[])
@patch(_WHICH, return_value=None)
def test_detect_binary_missing(_mock_which: MagicMock, _mock_fallbacks: MagicMock) -> None:
    with patch.dict(os.environ, {}, clear=True):
        probe = PiAdapter().detect()
    assert probe.installed is False
    assert probe.bin_path is None
    assert "Pi CLI not found" in probe.detail


# --------------------------------------------------------------------------- #
# build()
# --------------------------------------------------------------------------- #
@patch(_WHICH, return_value="/usr/bin/pi")
def test_build_print_mode_and_model_flag(_mock_which: MagicMock) -> None:
    inv = PiAdapter().build(prompt="hello", model="google/gemini-2.5-flash-lite", workspace="")
    assert inv.stdin is None
    assert inv.argv[0] == "/usr/bin/pi"
    assert "-p" in inv.argv
    assert "hello" in inv.argv
    assert "--model" in inv.argv
    idx = inv.argv.index("--model")
    assert inv.argv[idx + 1] == "google/gemini-2.5-flash-lite"
    assert inv.env is not None and inv.env.get("NO_COLOR") == "1"
    assert inv.cwd == os.getcwd()


@patch(_WHICH, return_value="/usr/bin/pi")
def test_build_omits_model_when_empty(_mock_which: MagicMock) -> None:
    inv = PiAdapter().build(prompt="p", model="", workspace="")
    assert "--model" not in inv.argv


@patch(_WHICH, return_value="/usr/bin/pi")
def test_build_forwards_provider_api_key(_mock_which: MagicMock) -> None:
    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "sk-gemini", "SOME_UNRELATED_SECRET": "nope"},
        clear=True,
    ):
        inv = PiAdapter().build(prompt="p", model="google/gemini-2.5-flash-lite", workspace="")
    assert inv.env is not None
    assert inv.env["GEMINI_API_KEY"] == "sk-gemini"
    assert "SOME_UNRELATED_SECRET" not in inv.env


@patch(_PI_FALLBACK_PATHS, return_value=[])
@patch(_WHICH, return_value=None)
def test_build_raises_when_binary_missing(
    _mock_which: MagicMock, _mock_fallbacks: MagicMock
) -> None:
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(RuntimeError, match="Pi CLI not found"),
    ):
        PiAdapter().build(prompt="p", model=None, workspace="")


# --------------------------------------------------------------------------- #
# registry / parse / explain_failure
# --------------------------------------------------------------------------- #
def test_pi_registry_entry() -> None:
    from integrations.llm_cli.registry import get_cli_provider_registration

    reg = get_cli_provider_registration("pi")
    assert reg is not None
    assert reg.model_env_key == "PI_MODEL"
    assert reg.adapter_factory().name == "pi"


def test_parse_strips_and_raises_on_empty() -> None:
    adapter = PiAdapter()
    assert adapter.parse(stdout="  pong  \n", stderr="", returncode=0) == "pong"
    with pytest.raises(RuntimeError, match="empty output"):
        adapter.parse(stdout="   ", stderr="", returncode=0)


def test_explain_failure_classifies_messages() -> None:
    adapter = PiAdapter()

    auth = adapter.explain_failure(stdout="", stderr="Error: not logged in", returncode=1)
    assert "Authentication failed" in auth

    model = adapter.explain_failure(stdout="", stderr="model not found: foo", returncode=1)
    assert "PI_MODEL format" in model

    quota = adapter.explain_failure(stdout="", stderr="rate limit exceeded", returncode=1)
    assert "Rate limited" in quota


# --------------------------------------------------------------------------- #
# runner integration (real adapter, mocked subprocess)
# --------------------------------------------------------------------------- #
@patch("integrations.llm_cli.runner.subprocess.run")
@patch(_PROBE, return_value=("pi 0.5.0", None))
@patch(_WHICH, return_value="/usr/bin/pi")
def test_cli_backed_client_invoke_forwards_pi_env(
    _mock_which: MagicMock, _mock_probe: MagicMock, mock_run: MagicMock
) -> None:
    from integrations.llm_cli.runner import CLIBackedLLMClient

    mock_run.return_value = MagicMock(returncode=0, stdout="answer\n", stderr="")

    with (
        patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr,
        patch.dict(os.environ, {"GEMINI_API_KEY": "sk-gemini"}, clear=False),
    ):
        gr.return_value.is_active = False
        client = CLIBackedLLMClient(
            PiAdapter(), model="google/gemini-2.5-flash-lite", max_tokens=256
        )
        resp = client.invoke("hello")

    assert resp.content == "answer"
    env = mock_run.call_args.kwargs["env"]
    assert env["GEMINI_API_KEY"] == "sk-gemini"
    assert env["NO_COLOR"] == "1"


# --------------------------------------------------------------------------- #
# live model test (real pi + real Gemini) — self-skips without creds/binary
# --------------------------------------------------------------------------- #
def _require_live_pi_gemini() -> str:
    """Skip unless the ``pi`` binary is installed and a Gemini key is present."""
    import shutil

    binary = shutil.which("pi") or os.environ.get("PI_BIN", "").strip()
    if not binary:
        pytest.skip("pi binary not installed; skipping live Pi test")
    if not os.environ.get("GEMINI_API_KEY", "").strip():
        pytest.skip("GEMINI_API_KEY not set; skipping live Pi test")
    return os.environ.get("PI_MODEL", "").strip() or "google/gemini-2.5-flash-lite"


@pytest.mark.integration
@pytest.mark.live_llm
def test_live_pi_gemini_round_trip() -> None:
    model = _require_live_pi_gemini()

    from integrations.llm_cli.runner import CLIBackedLLMClient

    adapter = PiAdapter()
    probe = adapter.detect()
    assert probe.installed is True, probe.detail
    assert probe.logged_in is not False, f"Pi reports not authenticated: {probe.detail}"

    client = CLIBackedLLMClient(adapter, model=model, max_tokens=256)
    resp = client.invoke("Reply with exactly one word: pong")

    assert resp.content.strip(), "Pi returned an empty response"
    assert "pong" in resp.content.lower()
