from __future__ import annotations

from app.cli.wizard.config import PROVIDER_BY_VALUE
from app.cli.wizard.env_sync import sync_provider_env


def test_sync_provider_env_updates_provider_specific_keys(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENV=development\n"
        "LLM_PROVIDER=anthropic\n"
        "OPENAI_API_KEY=old-key\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        api_key="new-key",
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "ENV=development\n" in content
    assert content.count("LLM_PROVIDER=") == 1
    assert "LLM_PROVIDER=openai\n" in content
    assert "OPENAI_API_KEY=new-key\n" in content
    assert "OPENAI_REASONING_MODEL=gpt-5-mini\n" in content
    assert "OPENAI_MODEL=gpt-5-mini\n" in content
