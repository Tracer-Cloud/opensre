"""A hosted gateway reaches the webapp's LLM routes from its task environment alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.account import account_llm_route
from config.constants.account import OPENSRE_ACCOUNT_METADATA_PATH_ENV, OPENSRE_ACCOUNT_TOKEN_ENV
from config.constants.billing import WEBAPP_URL_ENV
from core.llm.shared.openai_responses import uses_responses_api


@pytest.fixture(autouse=True)
def _no_local_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(OPENSRE_ACCOUNT_METADATA_PATH_ENV, str(tmp_path / "account.json"))
    monkeypatch.delenv(OPENSRE_ACCOUNT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(WEBAPP_URL_ENV, raising=False)


def test_the_injected_token_and_webapp_url_are_the_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, "osre_gw_org_A.signature")
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example/")

    route = account_llm_route()

    assert route is not None
    assert route.base_url == "https://app.example/api/llm/v1"
    # The served model family decides the OpenAI endpoint: a chat-completions
    # model name here makes every tool-using turn fail against gpt-5.6.
    assert uses_responses_api(route.model, "OPENAI_API_KEY")


@pytest.mark.parametrize("missing", [OPENSRE_ACCOUNT_TOKEN_ENV, WEBAPP_URL_ENV])
def test_either_half_missing_means_no_hosted_route(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.setenv(OPENSRE_ACCOUNT_TOKEN_ENV, "osre_gw_org_A.signature")
    monkeypatch.setenv(WEBAPP_URL_ENV, "https://app.example")
    monkeypatch.delenv(missing)

    assert account_llm_route() is None
