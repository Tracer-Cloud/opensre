"""Unit tests for the Grafana Tempo integration module."""

from integrations.catalog import load_env_integrations
from integrations.tempo import (
    TempoConfig,
    build_tempo_config,
    classify,
    tempo_config_from_env,
    tempo_extract_params,
    validate_tempo_config,
)


class TestTempoConfig:
    def test_defaults(self) -> None:
        config = TempoConfig()
        assert config.url == ""
        assert config.api_key == ""
        assert config.timeout_seconds == 10.0
        assert config.max_results == 20

    def test_is_configured_with_url_only(self) -> None:
        config = TempoConfig(url="http://localhost:3200")
        assert config.is_configured is True

    def test_is_configured_without_url(self) -> None:
        assert TempoConfig(api_key="token").is_configured is False

    def test_auth_headers_bearer(self) -> None:
        headers = TempoConfig(url="http://x", api_key="token").auth_headers()
        assert headers["Authorization"] == "Bearer token"
        assert headers["Accept"] == "application/json"

    def test_auth_headers_basic_and_org(self) -> None:
        headers = TempoConfig(
            url="http://x", username="u", password="p", org_id="42"
        ).auth_headers()
        assert headers["Authorization"].startswith("Basic ")
        assert headers["X-Scope-OrgID"] == "42"

    def test_auth_headers_none(self) -> None:
        headers = TempoConfig(url="http://x").auth_headers()
        assert "Authorization" not in headers


class TestBuildTempoConfig:
    def test_from_dict(self) -> None:
        config = build_tempo_config({"url": "http://tempo.example.com", "api_key": "secret"})
        assert config.url == "http://tempo.example.com"
        assert config.api_key == "secret"

    def test_from_none(self) -> None:
        assert build_tempo_config(None).is_configured is False


class TestTempoConfigFromEnv:
    def test_returns_none_without_url(self, monkeypatch) -> None:
        monkeypatch.delenv("TEMPO_URL", raising=False)
        assert tempo_config_from_env() is None

    def test_returns_config_with_url(self, monkeypatch) -> None:
        monkeypatch.setenv("TEMPO_URL", "http://localhost:3200")
        monkeypatch.setenv("TEMPO_API_KEY", "token")
        config = tempo_config_from_env()
        assert config is not None
        assert config.url == "http://localhost:3200"
        assert config.api_key == "token"
        assert config.is_configured is True

    def test_returns_config_with_basic_auth(self, monkeypatch) -> None:
        monkeypatch.setenv("TEMPO_URL", "http://localhost:3200")
        monkeypatch.setenv("TEMPO_USERNAME", "admin")
        monkeypatch.setenv("TEMPO_PASSWORD", "secret")
        monkeypatch.delenv("TEMPO_API_KEY", raising=False)
        config = tempo_config_from_env()
        assert config is not None
        assert config.username == "admin"
        assert config.password == "secret"
        assert config.api_key == ""

    def test_returns_config_with_org_id(self, monkeypatch) -> None:
        monkeypatch.setenv("TEMPO_URL", "http://localhost:3200")
        monkeypatch.setenv("TEMPO_ORG_ID", "tenant-1")
        config = tempo_config_from_env()
        assert config is not None
        assert config.org_id == "tenant-1"


class TestTempoValidation:
    def test_validate_requires_url(self) -> None:
        result = validate_tempo_config(TempoConfig())
        assert result.ok is False
        assert "TEMPO_URL" in result.detail

    def test_validate_hits_search_tags(self, monkeypatch) -> None:
        class _FakeResponse:
            def raise_for_status(self) -> None:
                return None

        captured: dict[str, object] = {}

        def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return _FakeResponse()

        monkeypatch.setattr("integrations.tempo.httpx.get", _fake_get)

        result = validate_tempo_config(TempoConfig(url="http://localhost:3200"))
        assert result.ok is True
        assert str(captured["url"]).endswith("/api/search/tags")


class TestTempoExtractParams:
    def test_extracts_params(self) -> None:
        params = tempo_extract_params(
            {"tempo": {"url": "http://tempo.example.com", "api_key": "key"}}
        )
        assert params["url"] == "http://tempo.example.com"
        assert params["api_key"] == "key"

    def test_uses_defaults_when_missing(self) -> None:
        params = tempo_extract_params({})
        assert params["url"] == ""
        assert params["api_key"] == ""

    def test_normalizes_optional_null_credentials_from_setup(self) -> None:
        credentials = {
            "url": "http://localhost:3200",
            "api_key": None,
            "username": None,
            "password": None,
            "org_id": None,
        }

        config, source = classify(credentials, "tempo-local")
        params = tempo_extract_params({"tempo": credentials})

        assert source == "tempo"
        assert config is not None
        assert config.api_key == ""
        assert params["api_key"] == ""
        assert params["username"] == ""
        assert params["password"] == ""
        assert params["org_id"] == ""


class TestTempoEnvCatalogLoading:
    def test_loads_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("TEMPO_URL", "http://localhost:3200")
        records = load_env_integrations()
        tempo_records = [r for r in records if r.get("service") == "tempo"]
        assert len(tempo_records) == 1
        assert tempo_records[0]["credentials"]["url"] == "http://localhost:3200"
