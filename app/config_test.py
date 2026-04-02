from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import LLMSettings


def test_llm_settings_reject_provider_typos_with_suggestion() -> None:
    with pytest.raises(ValidationError, match="Did you mean 'openai'"):
        LLMSettings.model_validate({
            "provider": "opneai",
            "openai_api_key": "sk-test",
        })


def test_llm_settings_require_api_key_for_selected_provider() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        LLMSettings.model_validate({"provider": "openai"})
