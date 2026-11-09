"""Unit tests for the Kafka integration module."""

from typing import Any

import pytest

from integrations.kafka import (
    KafkaConfig,
    KafkaValidationResult,
    build_kafka_config,
    get_consumer_group_lag,
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


class _FakeTopicPartitionOffset:
    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self.topic = topic
        self.partition = partition
        self.offset = offset
        self.error = None


class _FakeGroupResult:
    def __init__(self, topic_partitions: list[Any]) -> None:
        self.topic_partitions = topic_partitions


class _FakeFuture:
    def __init__(self, result: Any) -> None:
        self._result = result

    def result(self) -> Any:
        return self._result


class _FakeAdminClient:
    """Records the exact object passed to list_consumer_group_offsets.

    That object's *type* is what proves which import branch
    (confluent_kafka._model vs confluent_kafka.admin) get_consumer_group_lag
    actually took — it constructs ConsumerGroupTopicPartitions(group_id) from
    whichever location it imported and passes that instance straight through.
    """

    def __init__(self) -> None:
        self.captured_request: Any = None

    def list_consumer_group_offsets(self, requests: list[Any]) -> dict[str, _FakeFuture]:
        self.captured_request = requests[0]
        offset = _FakeTopicPartitionOffset("verify-topic", 0, offset=5)
        return {"verify-group": _FakeFuture(_FakeGroupResult([offset]))}


class _FakeConsumer:
    def get_watermark_offsets(self, _tp: Any, timeout: float) -> tuple[int, int]:
        # get_consumer_group_lag calls this with timeout= as a keyword, so
        # the parameter name must match even though the value is unused.
        del timeout
        return (0, 20)

    def close(self) -> None:
        pass


class TestGetConsumerGroupLagImportCompat:
    """Pins which confluent_kafka location ConsumerGroupTopicPartitions
    resolves from, per the two branches in get_consumer_group_lag's nested
    try/except. Both branches construct the SAME callable
    (ConsumerGroupTopicPartitions(group_id)) and pass it straight through to
    admin.list_consumer_group_offsets, so asserting the *type* of the object
    the fake admin client actually received is what proves the executed
    branch, not just that lag math still works (which would pass even if the
    import silently resolved to the wrong location, or none at all).
    """

    def _config(self) -> KafkaConfig:
        return KafkaConfig(bootstrap_servers="broker:9092")

    def test_uses_the_model_location_when_available(self, monkeypatch: Any) -> None:
        pytest.importorskip("confluent_kafka")
        from confluent_kafka._model import ConsumerGroupTopicPartitions as ModelClass

        admin = _FakeAdminClient()
        monkeypatch.setattr("integrations.kafka._get_admin_client", lambda _config: admin)
        monkeypatch.setattr("integrations.kafka._get_consumer", lambda _config: _FakeConsumer())

        result = get_consumer_group_lag(self._config(), group_id="verify-group")

        assert result["available"] is True
        assert isinstance(admin.captured_request, ModelClass)

    def test_falls_back_to_the_admin_location_when_model_lacks_it(self, monkeypatch: Any) -> None:
        """Simulates an older confluent-kafka where the class is only public
        under confluent_kafka.admin, by hiding it from confluent_kafka._model
        and installing a distinct stand-in class at the admin location, so
        the assertion (isinstance of *that* stand-in) can only pass if the
        except branch actually ran, not the try branch.
        """
        pytest.importorskip("confluent_kafka")
        import confluent_kafka._model as model_module
        import confluent_kafka.admin as admin_module

        monkeypatch.delattr(model_module, "ConsumerGroupTopicPartitions", raising=False)

        class _FallbackConsumerGroupTopicPartitions:
            def __init__(self, group_id: str) -> None:
                self.group_id = group_id

        monkeypatch.setattr(
            admin_module,
            "ConsumerGroupTopicPartitions",
            _FallbackConsumerGroupTopicPartitions,
            raising=False,
        )

        admin = _FakeAdminClient()
        monkeypatch.setattr("integrations.kafka._get_admin_client", lambda _config: admin)
        monkeypatch.setattr("integrations.kafka._get_consumer", lambda _config: _FakeConsumer())

        result = get_consumer_group_lag(self._config(), group_id="verify-group")

        assert result["available"] is True
        assert isinstance(admin.captured_request, _FallbackConsumerGroupTopicPartitions)
        assert admin.captured_request.group_id == "verify-group"

    def test_raises_clean_error_when_neither_location_has_it(self, monkeypatch: Any) -> None:
        """Both locations missing the class is a genuinely broken confluent-kafka
        install. Confirms that case fails closed through the outer except
        (tool_unavailable), not with an unrelated traceback.
        """
        pytest.importorskip("confluent_kafka")
        import confluent_kafka._model as model_module
        import confluent_kafka.admin as admin_module

        monkeypatch.delattr(model_module, "ConsumerGroupTopicPartitions", raising=False)
        monkeypatch.delattr(admin_module, "ConsumerGroupTopicPartitions", raising=False)

        result = get_consumer_group_lag(self._config(), group_id="verify-group")

        assert result["available"] is False
        assert "ConsumerGroupTopicPartitions" in result["error"]
