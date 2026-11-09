"""A gateway on the webapp's hosted LLM route is metered per token, not per turn."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.constants.account import OPENSRE_ACCOUNT_METADATA_PATH_ENV, OPENSRE_ACCOUNT_TOKEN_ENV
from config.constants.billing import WEBAPP_URL_ENV
from gateway.core.billing import turn_metering


def test_a_turn_is_admitted_without_a_second_per_turn_charge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(OPENSRE_ACCOUNT_METADATA_PATH_ENV, str(tmp_path / "none.json"))
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, "osre_gw_org_A.signature")
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example")
    charged: list[str] = []

    def consume(*_args: object, **_kwargs: object) -> object:
        charged.append("per-turn")
        raise AssertionError("the hosted LLM route already meters this turn")

    monkeypatch.setattr(turn_metering, "consume_credits", consume)

    with turn_metering.bound_turn_metering(
        organization_id="org_A", reason="slack_turn", idempotency_key="ts-1", on_denied=list
    ):
        assert turn_metering.admit_metered_turn() is True
    assert charged == []
