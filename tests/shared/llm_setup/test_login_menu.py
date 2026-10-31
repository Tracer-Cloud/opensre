"""Connection management remains available when saved credentials stop working."""

from __future__ import annotations

import pytest

from config.llm_auth.records import save_provider_auth_record
from surfaces.shared.llm_setup import login_menu


@pytest.mark.parametrize("action", ["verify", "disconnect"])
def test_saved_cloud_connection_remains_manageable_without_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    save_provider_auth_record(
        provider="vertex-ai",
        auth_name="vertex-ai",
        kind="none",
        source="ambient",
        detail="Previously connected with ADC",
    )
    offered = []

    def choose(**kwargs):
        offered.extend(name for name, _ in kwargs["options"])
        return "vertex-ai"

    monkeypatch.setattr(login_menu, "repl_choose_subcommand", choose)
    assert login_menu.prompt_connection_target(action) == "vertex-ai"
    assert "vertex-ai" in offered
