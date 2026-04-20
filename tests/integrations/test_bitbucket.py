"""Unit tests for the Bitbucket integration module."""

from app.integrations.bitbucket import (
    BitbucketConfig,
    BitbucketValidationResult,
    bitbucket_config_from_env,
    build_bitbucket_config,
)


class TestBitbucketConfig:
    """Tests for BitbucketConfig model."""

    def test_defaults(self) -> None:
        config = BitbucketConfig(workspace="myteam", username="user", app_password="pass")
        assert config.workspace == "myteam"
        assert config.username == "user"
        assert config.app_password == "pass"
        assert config.base_url == "https://api.bitbucket.org/2.0"
        assert config.timeout_seconds == 10.0
        assert config.max_results == 25

    def test_is_configured_with_all_fields(self) -> None:
        config = BitbucketConfig(workspace="myteam", username="user", app_password="pass")
        assert config.is_configured is True

    def test_is_configured_missing_workspace(self) -> None:
        config = BitbucketConfig(username="user", app_password="pass")
        assert config.is_configured is False

    def test_is_configured_missing_password(self) -> None:
        config = BitbucketConfig(workspace="myteam", username="user")
        assert config.is_configured is False

    def test_normalize_workspace_strips_whitespace(self) -> None:
        config = BitbucketConfig(workspace="  myteam  ", username="user", app_password="pass")
        assert config.workspace == "myteam"

    def test_normalize_base_url_strips_trailing_slash(self) -> None:
        config = BitbucketConfig(
            workspace="myteam",
            username="user",
            app_password="pass",
            base_url="https://api.bitbucket.org/2.0/",
        )
        assert config.base_url == "https://api.bitbucket.org/2.0"


class TestBuildBitbucketConfig:
    """Tests for build_bitbucket_config helper."""

    def test_from_dict(self) -> None:
        config = build_bitbucket_config(
            {"workspace": "myteam", "username": "user", "app_password": "pass"}
        )
        assert config.workspace == "myteam"
        assert config.is_configured is True

    def test_from_none(self) -> None:
        config = build_bitbucket_config(None)
        assert config.workspace == ""
        assert config.is_configured is False


class TestBitbucketConfigFromEnv:
    """Tests for bitbucket_config_from_env helper."""

    def test_returns_none_without_workspace(self) -> None:
        import os

        old = os.environ.get("BITBUCKET_WORKSPACE")
        os.environ.pop("BITBUCKET_WORKSPACE", None)
        try:
            result = bitbucket_config_from_env()
            assert result is None
        finally:
            if old is not None:
                os.environ["BITBUCKET_WORKSPACE"] = old

    def test_returns_config_with_workspace(self) -> None:
        import os

        os.environ["BITBUCKET_WORKSPACE"] = "testteam"
        os.environ["BITBUCKET_USERNAME"] = "testuser"
        os.environ["BITBUCKET_APP_PASSWORD"] = "testpass"
        try:
            config = bitbucket_config_from_env()
            assert config is not None
            assert config.workspace == "testteam"
            assert config.username == "testuser"
            assert config.app_password == "testpass"
        finally:
            for key in [
                "BITBUCKET_WORKSPACE",
                "BITBUCKET_USERNAME",
                "BITBUCKET_APP_PASSWORD",
            ]:
                os.environ.pop(key, None)


class TestBitbucketValidationResult:
    """Tests for BitbucketValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = BitbucketValidationResult(ok=True, detail="Connected.")
        assert result.ok is True

    def test_error_result(self) -> None:
        result = BitbucketValidationResult(ok=False, detail="Auth failed.")
        assert result.ok is False
