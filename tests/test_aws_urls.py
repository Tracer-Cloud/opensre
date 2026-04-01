"""Tests for AWS console URL generators."""

from __future__ import annotations

from urllib.parse import urlparse

from app.agent.nodes.publish_findings.urls.aws import (
    _encode_aws_path,
    build_batch_console_url,
    build_cloudwatch_url,
    build_datadog_logs_url,
    build_ecs_console_url,
    build_grafana_explore_url,
    build_lambda_console_url,
    build_s3_console_url,
)


class TestEncodeAwsPath:
    """Tests for _encode_aws_path helper."""

    def test_replaces_slashes(self) -> None:
        assert _encode_aws_path("/aws/lambda/my-func") == "$252Faws$252Flambda$252Fmy-func"

    def test_no_slashes_unchanged(self) -> None:
        assert _encode_aws_path("no-slashes-here") == "no-slashes-here"

    def test_empty_string(self) -> None:
        assert _encode_aws_path("") == ""

    def test_multiple_consecutive_slashes(self) -> None:
        assert _encode_aws_path("a//b") == "a$252F$252Fb"


class TestBuildCloudwatchUrl:
    """Tests for build_cloudwatch_url."""

    def test_returns_prebuilt_url_if_present(self) -> None:
        ctx = {"cloudwatch_logs_url": "https://already-built.example.com"}
        assert build_cloudwatch_url(ctx) == "https://already-built.example.com"

    def test_prebuilt_url_takes_priority_over_components(self) -> None:
        ctx = {
            "cloudwatch_logs_url": "https://prebuilt.example.com",
            "cloudwatch_log_group": "/aws/lambda/func",
            "cloudwatch_log_stream": "stream-1",
        }
        assert build_cloudwatch_url(ctx) == "https://prebuilt.example.com"

    def test_returns_none_when_no_data(self) -> None:
        assert build_cloudwatch_url({}) is None

    def test_returns_none_when_group_missing(self) -> None:
        ctx = {"cloudwatch_log_stream": "stream-1", "cloudwatch_region": "eu-west-1"}
        assert build_cloudwatch_url(ctx) is None

    def test_builds_url_with_group_and_stream(self) -> None:
        ctx = {
            "cloudwatch_log_group": "/aws/lambda/my-func",
            "cloudwatch_log_stream": "2024/01/01/[$LATEST]abc",
            "cloudwatch_region": "us-west-2",
        }
        url = build_cloudwatch_url(ctx)
        assert url is not None
        parsed = urlparse(url)
        assert parsed.netloc == "us-west-2.console.aws.amazon.com"
        assert parsed.path.startswith("/cloudwatch")
        assert "region=us-west-2" in url
        assert "$252Faws$252Flambda$252Fmy-func" in url
        assert "log-events/" in url

    def test_builds_url_with_group_only(self) -> None:
        ctx = {
            "cloudwatch_log_group": "/ecs/my-service",
            "cloudwatch_region": "ap-south-1",
        }
        url = build_cloudwatch_url(ctx)
        assert url is not None
        parsed = urlparse(url)
        assert parsed.netloc == "ap-south-1.console.aws.amazon.com"
        assert parsed.path.startswith("/cloudwatch")
        assert "$252Fecs$252Fmy-service" in url
        assert "log-events" not in url

    def test_defaults_to_us_east_1(self) -> None:
        ctx = {"cloudwatch_log_group": "/my/group"}
        url = build_cloudwatch_url(ctx)
        assert url is not None
        assert urlparse(url).netloc == "us-east-1.console.aws.amazon.com"
        assert "region=us-east-1" in url

    def test_empty_region_defaults_to_us_east_1(self) -> None:
        ctx = {"cloudwatch_log_group": "/my/group", "cloudwatch_region": ""}
        url = build_cloudwatch_url(ctx)
        assert url is not None
        assert "us-east-1" in url


class TestBuildS3ConsoleUrl:
    """Tests for build_s3_console_url."""

    def test_basic_url(self) -> None:
        url = build_s3_console_url("my-bucket", "path/to/file.csv")
        parsed = urlparse(url)
        assert parsed.netloc == "s3.console.aws.amazon.com"
        assert parsed.path.startswith("/s3/object/my-bucket")
        assert "region=us-east-1" in url
        assert "prefix=path%2Fto%2Ffile.csv" in url

    def test_custom_region(self) -> None:
        url = build_s3_console_url("bucket", "key", region="eu-central-1")
        assert "region=eu-central-1" in url

    def test_special_characters_in_key(self) -> None:
        url = build_s3_console_url("bucket", "path/with spaces/file (1).txt")
        # Spaces and parens should be URL-encoded
        assert "%20" in url or "+" in url
        assert "path" in url


class TestBuildLambdaConsoleUrl:
    """Tests for build_lambda_console_url."""

    def test_basic_url(self) -> None:
        url = build_lambda_console_url("my-function")
        parsed = urlparse(url)
        assert parsed.netloc == "us-east-1.console.aws.amazon.com"
        assert parsed.path.startswith("/lambda")
        assert "functions/my-function" in url
        assert "tab=code" in url

    def test_custom_region_and_tab(self) -> None:
        url = build_lambda_console_url("func", region="ap-south-1", tab="monitoring")
        assert "ap-south-1" in url
        assert "tab=monitoring" in url


class TestBuildEcsConsoleUrl:
    """Tests for build_ecs_console_url."""

    def test_basic_url(self) -> None:
        url = build_ecs_console_url("prod-cluster")
        parsed = urlparse(url)
        assert parsed.netloc == "us-east-1.console.aws.amazon.com"
        assert parsed.path.startswith("/ecs/v2/clusters/prod-cluster")
        assert "region=us-east-1" in url

    def test_custom_region(self) -> None:
        url = build_ecs_console_url("cluster", region="eu-west-1")
        assert "eu-west-1" in url


class TestBuildBatchConsoleUrl:
    """Tests for build_batch_console_url."""

    def test_basic_url(self) -> None:
        url = build_batch_console_url("my-queue")
        parsed = urlparse(url)
        assert parsed.netloc == "us-east-1.console.aws.amazon.com"
        assert parsed.path.startswith("/batch")
        assert "queues/detail/my-queue" in url

    def test_custom_region(self) -> None:
        url = build_batch_console_url("queue", region="us-west-2")
        assert "us-west-2" in url


class TestBuildGrafanaExploreUrl:
    """Tests for build_grafana_explore_url."""

    def test_returns_none_when_endpoint_empty(self) -> None:
        assert build_grafana_explore_url("", '{job="app"}') is None

    def test_basic_url(self) -> None:
        url = build_grafana_explore_url("https://myorg.grafana.net", '{job="app"}')
        assert url is not None
        assert "https://myorg.grafana.net/explore" in url
        assert "loki" in url

    def test_strips_trailing_slash(self) -> None:
        url = build_grafana_explore_url("https://grafana.example.com/", "{app}")
        assert url is not None
        assert "grafana.example.com/explore" in url
        assert "grafana.example.com//explore" not in url


class TestBuildDatadogLogsUrl:
    """Tests for build_datadog_logs_url."""

    def test_default_site(self) -> None:
        url = build_datadog_logs_url("service:web")
        assert "app.datadoghq.com/logs" in url
        assert "query=service%3Aweb" in url

    def test_custom_site(self) -> None:
        url = build_datadog_logs_url("env:prod", site="datadoghq.eu")
        assert "app.datadoghq.eu/logs" in url

    def test_query_with_spaces(self) -> None:
        url = build_datadog_logs_url("service:web status:error")
        assert "%20" in url
