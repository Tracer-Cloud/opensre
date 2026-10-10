"""Unit tests for the SigNoz integration module."""

from integrations.catalog import load_env_integrations
from integrations.signoz import (
    SigNozConfig,
    build_signoz_config,
    signoz_config_from_env,
    signoz_extract_params,
    signoz_is_available,
    validate_signoz_config,
)


class TestSigNozConfig:
    def test_defaults(self) -> None:
        config = SigNozConfig()
        assert config.url == ""
        assert config.api_key == ""
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_url_and_key(self) -> None:
        config = SigNozConfig(url="http://localhost:8080", api_key="test-key")
        assert config.is_configured is True

    def test_is_configured_without_credentials(self) -> None:
        config = SigNozConfig(url="http://localhost:8080")
        assert config.is_configured is False
        config = SigNozConfig(api_key="test-key")
        assert config.is_configured is False


class TestBuildSigNozConfig:
    def test_from_dict(self) -> None:
        config = build_signoz_config({"url": "http://signoz.example.com", "api_key": "secret"})
        assert config.url == "http://signoz.example.com"
        assert config.api_key == "secret"

    def test_from_none(self) -> None:
        config = build_signoz_config(None)
        assert config.is_configured is False


class TestSigNozConfigFromEnv:
    def test_returns_none_without_credentials(self) -> None:
        import os

        for key in ("SIGNOZ_URL", "SIGNOZ_API_KEY"):
            os.environ.pop(key, None)
        assert signoz_config_from_env() is None

    def test_returns_config_with_url_and_key(self) -> None:
        import os

        os.environ["SIGNOZ_URL"] = "http://localhost:8080"
        os.environ["SIGNOZ_API_KEY"] = "api-key"
        try:
            config = signoz_config_from_env()
            assert config is not None
            assert config.url == "http://localhost:8080"
            assert config.api_key == "api-key"
            assert config.is_configured is True
        finally:
            os.environ.pop("SIGNOZ_URL", None)
            os.environ.pop("SIGNOZ_API_KEY", None)


class TestSigNozValidation:
    def test_validate_requires_credentials(self) -> None:
        result = validate_signoz_config(SigNozConfig())
        assert result.ok is False
        assert "SIGNOZ_URL" in result.detail

    def test_validate_query_api_mode(self, monkeypatch) -> None:
        class _FakeResponse:
            def raise_for_status(self) -> None:
                return None

        captured: dict[str, object] = {}

        def _fake_get(url: str, **kwargs: object) -> _FakeResponse:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return _FakeResponse()

        monkeypatch.setattr("integrations.signoz.config.httpx.get", _fake_get)

        config = SigNozConfig(url="http://localhost:8080", api_key="test-key")
        result = validate_signoz_config(config)

        assert result.ok is True
        assert "Query API" in result.detail
        assert str(captured["url"]).endswith("/api/v2/metrics")


class TestSigNozExtractParams:
    def test_extracts_params(self) -> None:
        sources = {
            "signoz": {
                "url": "http://signoz.example.com",
                "api_key": "key",
            }
        }
        params = signoz_extract_params(sources)
        assert params["url"] == "http://signoz.example.com"
        assert params["api_key"] == "key"

    def test_uses_defaults_when_missing(self) -> None:
        params = signoz_extract_params({})
        assert params["url"] == ""
        assert params["api_key"] == ""


class TestSigNozIsAvailable:
    def test_available_when_connection_verified(self) -> None:
        assert signoz_is_available({"signoz": {"connection_verified": True}}) is True

    def test_unavailable_without_connection_verified(self) -> None:
        assert signoz_is_available({"signoz": {"url": "http://localhost:8080"}}) is False


class TestSigNozEnvCatalogLoading:
    def test_loads_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("SIGNOZ_URL", "http://localhost:8080")
        monkeypatch.setenv("SIGNOZ_API_KEY", "test-key")
        records = load_env_integrations()
        signoz_records = [r for r in records if r.get("service") == "signoz"]
        assert len(signoz_records) == 1
        assert signoz_records[0]["credentials"]["url"] == "http://localhost:8080"
