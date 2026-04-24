from __future__ import annotations

from app.services import llm_client


class _FakeAnthropicMessages:
    def create(self, **_kwargs):
        raise AssertionError("unexpected network call in unit test")


class _FakeAnthropic:
    last_api_key: str | None = None

    def __init__(self, *, api_key: str, timeout: float) -> None:
        _FakeAnthropic.last_api_key = api_key
        self.timeout = timeout
        self.messages = _FakeAnthropicMessages()


class _FakeOpenAICompletions:
    def create(self, **_kwargs):
        raise AssertionError("unexpected network call in unit test")


class _FakeOpenAIChat:
    def __init__(self) -> None:
        self.completions = _FakeOpenAICompletions()


class _FakeOpenAI:
    last_api_key: str | None = None
    last_base_url: str | None = None

    def __init__(self, *, api_key: str, base_url: str | None = None, timeout: float) -> None:
        _FakeOpenAI.last_api_key = api_key
        _FakeOpenAI.last_base_url = base_url
        self.base_url = base_url
        self.timeout = timeout
        self.chat = _FakeOpenAIChat()


class _FakeAzureOpenAI:
    last_api_key: str | None = None
    last_endpoint: str | None = None
    last_api_version: str | None = None
    last_token_provider: bool = False

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_version: str | None = None,
        azure_endpoint: str | None = None,
        azure_ad_token_provider=None,
        timeout: float | None = None,
    ) -> None:
        _FakeAzureOpenAI.last_api_key = api_key
        _FakeAzureOpenAI.last_endpoint = azure_endpoint
        _FakeAzureOpenAI.last_api_version = api_version
        _FakeAzureOpenAI.last_token_provider = azure_ad_token_provider is not None
        self.api_version = api_version
        self.azure_endpoint = azure_endpoint
        self.timeout = timeout
        self.chat = _FakeOpenAIChat()


def test_openai_llm_client_reads_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "stored-openai-key" if env_var == "OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    client = llm_client.OpenAILLMClient(model="gpt-5.4")
    client._ensure_client()

    assert _FakeOpenAI.last_api_key == "stored-openai-key"


def test_anthropic_llm_client_reads_secure_local_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "stored-anthropic-key" if env_var == "ANTHROPIC_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "Anthropic", _FakeAnthropic)

    client = llm_client.LLMClient(model="claude-opus-4")
    client._ensure_client()

    assert _FakeAnthropic.last_api_key == "stored-anthropic-key"


def test_minimax_llm_client_reads_api_key_and_base_url(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "minimax-test-key" if env_var == "MINIMAX_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    client = llm_client.OpenAILLMClient(
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        temperature=1.0,
    )
    client._ensure_client()

    assert _FakeOpenAI.last_api_key == "minimax-test-key"
    assert _FakeOpenAI.last_base_url == "https://api.minimax.io/v1"


def test_minimax_llm_client_temperature_is_set(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "minimax-test-key" if env_var == "MINIMAX_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "OpenAI", _FakeOpenAI)

    client = llm_client.OpenAILLMClient(
        model="MiniMax-M2.7",
        base_url="https://api.minimax.io/v1",
        api_key_env="MINIMAX_API_KEY",
        temperature=1.0,
    )
    assert client._temperature == 1.0


def test_azure_openai_llm_client_with_api_key(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "test-azure-key" if env_var == "AZURE_OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "AzureOpenAI", _FakeAzureOpenAI)

    llm_client.AzureOpenAILLMClient(
        model="gpt-4-turbo",
        azure_endpoint="https://myresource.openai.azure.com",
        deployment_name="gpt-4-turbo",
        api_key="test-azure-key",
    )

    assert _FakeAzureOpenAI.last_api_key == "test-azure-key"
    assert _FakeAzureOpenAI.last_endpoint == "https://myresource.openai.azure.com"
    assert _FakeAzureOpenAI.last_token_provider is False


def test_azure_openai_llm_client_with_managed_identity(monkeypatch) -> None:
    """Test Azure OpenAI client using DefaultAzureCredential (managed identity)."""

    def fake_token_provider(*args, **kwargs):
        return "fake-token"

    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "",  # No API key set
    )
    monkeypatch.setattr(llm_client, "AzureOpenAI", _FakeAzureOpenAI)
    monkeypatch.setattr(
        llm_client,
        "DefaultAzureCredential",
        lambda: type("FakeCredential", (), {"get_token": fake_token_provider})(),
    )

    llm_client.AzureOpenAILLMClient(
        model="gpt-4-turbo",
        azure_endpoint="https://myresource.openai.azure.com",
        deployment_name="gpt-4-turbo",
        api_key=None,  # Use managed identity
    )

    assert _FakeAzureOpenAI.last_api_key is None
    assert _FakeAzureOpenAI.last_endpoint == "https://myresource.openai.azure.com"
    assert _FakeAzureOpenAI.last_token_provider is True


def test_azure_openai_llm_client_api_version(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_client,
        "resolve_llm_api_key",
        lambda env_var: "test-azure-key" if env_var == "AZURE_OPENAI_API_KEY" else "",
    )
    monkeypatch.setattr(llm_client, "AzureOpenAI", _FakeAzureOpenAI)

    client = llm_client.AzureOpenAILLMClient(
        model="gpt-4-turbo",
        azure_endpoint="https://myresource.openai.azure.com",
        deployment_name="gpt-4-turbo",
        api_key="test-azure-key",
        api_version="2025-04-01",
    )

    assert _FakeAzureOpenAI.last_api_version == "2025-04-01"
    assert client._api_version == "2025-04-01"
