"""Unit tests for the Kafka integration module."""

from app.integrations.kafka import (
    KafkaConfig,
    KafkaValidationResult,
    build_kafka_config,
    kafka_config_from_env,
)


class TestKafkaConfig:
    """Tests for KafkaConfig model."""

    def test_defaults(self) -> None:
        config = KafkaConfig(bootstrap_servers="localhost:9092")
        assert config.bootstrap_servers == "localhost:9092"
        assert config.security_protocol == "PLAINTEXT"
        assert config.sasl_mechanism == ""
        assert config.sasl_username == ""
        assert config.sasl_password == ""
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_servers(self) -> None:
        config = KafkaConfig(bootstrap_servers="broker1:9092,broker2:9092")
        assert config.is_configured is True

    def test_is_configured_without_servers(self) -> None:
        config = KafkaConfig()
        assert config.is_configured is False

    def test_normalize_bootstrap_servers_strips_whitespace(self) -> None:
        config = KafkaConfig(bootstrap_servers="  broker:9092  ")
        assert config.bootstrap_servers == "broker:9092"

    def test_normalize_security_protocol_uppercase(self) -> None:
        config = KafkaConfig(bootstrap_servers="localhost:9092", security_protocol="sasl_ssl")
        assert config.security_protocol == "SASL_SSL"

    def test_normalize_empty_security_protocol_uses_default(self) -> None:
        config = KafkaConfig(bootstrap_servers="localhost:9092", security_protocol="")
        assert config.security_protocol == "PLAINTEXT"

    def test_sasl_config(self) -> None:
        config = KafkaConfig(
            bootstrap_servers="broker:9092",
            security_protocol="SASL_SSL",
            sasl_mechanism="PLAIN",
            sasl_username="user",
            sasl_password="pass",
        )
        assert config.sasl_mechanism == "PLAIN"
        assert config.sasl_username == "user"
        assert config.sasl_password == "pass"


class TestBuildKafkaConfig:
    """Tests for build_kafka_config helper."""

    def test_from_dict(self) -> None:
        config = build_kafka_config({"bootstrap_servers": "broker:9092"})
        assert config.bootstrap_servers == "broker:9092"
        assert config.is_configured is True

    def test_from_none(self) -> None:
        config = build_kafka_config(None)
        assert config.bootstrap_servers == ""
        assert config.is_configured is False


class TestKafkaConfigFromEnv:
    """Tests for kafka_config_from_env helper."""

    def test_returns_none_without_servers(self) -> None:
        import os

        old = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
        os.environ.pop("KAFKA_BOOTSTRAP_SERVERS", None)
        try:
            result = kafka_config_from_env()
            assert result is None
        finally:
            if old is not None:
                os.environ["KAFKA_BOOTSTRAP_SERVERS"] = old

    def test_returns_config_with_servers(self) -> None:
        import os

        os.environ["KAFKA_BOOTSTRAP_SERVERS"] = "broker1:9092,broker2:9092"
        os.environ["KAFKA_SECURITY_PROTOCOL"] = "SASL_SSL"
        os.environ["KAFKA_SASL_MECHANISM"] = "PLAIN"
        os.environ["KAFKA_SASL_USERNAME"] = "testuser"
        os.environ["KAFKA_SASL_PASSWORD"] = "testpass"
        try:
            config = kafka_config_from_env()
            assert config is not None
            assert config.bootstrap_servers == "broker1:9092,broker2:9092"
            assert config.security_protocol == "SASL_SSL"
            assert config.sasl_mechanism == "PLAIN"
            assert config.sasl_username == "testuser"
            assert config.sasl_password == "testpass"
        finally:
            for key in [
                "KAFKA_BOOTSTRAP_SERVERS",
                "KAFKA_SECURITY_PROTOCOL",
                "KAFKA_SASL_MECHANISM",
                "KAFKA_SASL_USERNAME",
                "KAFKA_SASL_PASSWORD",
            ]:
                os.environ.pop(key, None)


class TestKafkaValidationResult:
    """Tests for KafkaValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = KafkaValidationResult(ok=True, detail="Connected.")
        assert result.ok is True

    def test_error_result(self) -> None:
        result = KafkaValidationResult(ok=False, detail="Connection refused.")
        assert result.ok is False
