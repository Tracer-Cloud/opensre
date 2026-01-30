"""Unit tests for data source detection."""

from app.agent.nodes.investigate.data_sources import detect_available_sources
from app.agent.state import InvestigationState


def test_detect_cloudwatch_sources_from_annotations():
    """Test CloudWatch detection from annotations."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
                "cloudwatch_log_stream": "job-12345/container-name/abc123",
                "cloudwatch_region": "us-west-2",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "cloudwatch" in sources
    assert sources["cloudwatch"]["log_group"] == "/aws/batch/job"
    assert sources["cloudwatch"]["log_stream"] == "job-12345/container-name/abc123"
    assert sources["cloudwatch"]["region"] == "us-west-2"


def test_detect_cloudwatch_sources_from_common_annotations():
    """Test CloudWatch detection from commonAnnotations."""
    state: InvestigationState = {
        "raw_alert": {
            "commonAnnotations": {
                "cloudwatch_log_group": "/aws/lambda/my-function",
                "cloudwatch_log_stream": "2024/01/30/[$LATEST]abc123",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "cloudwatch" in sources
    assert sources["cloudwatch"]["log_group"] == "/aws/lambda/my-function"
    assert sources["cloudwatch"]["log_stream"] == "2024/01/30/[$LATEST]abc123"
    assert sources["cloudwatch"]["region"] == "us-east-1"  # default


def test_detect_cloudwatch_sources_alternative_names():
    """Test CloudWatch detection with alternative naming conventions."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "log_group": "/aws/batch/job",
                "log_stream": "job-12345/container-name/abc123",
                "aws_region": "eu-west-1",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "cloudwatch" in sources
    assert sources["cloudwatch"]["log_group"] == "/aws/batch/job"
    assert sources["cloudwatch"]["log_stream"] == "job-12345/container-name/abc123"
    assert sources["cloudwatch"]["region"] == "eu-west-1"


def test_detect_s3_sources():
    """Test S3 detection from annotations."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "s3_bucket": "my-data-bucket",
                "s3_prefix": "raw/events/2024/01/",
                "s3_key": "events.parquet",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "s3" in sources
    assert sources["s3"]["bucket"] == "my-data-bucket"
    assert sources["s3"]["prefix"] == "raw/events/2024/01/"
    assert sources["s3"]["key"] == "events.parquet"


def test_detect_s3_sources_alternative_names():
    """Test S3 detection with alternative naming."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "bucket": "my-data-bucket",
                "prefix": "raw/events/2024/01/",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "s3" in sources
    assert sources["s3"]["bucket"] == "my-data-bucket"
    assert sources["s3"]["prefix"] == "raw/events/2024/01/"
    assert "key" not in sources["s3"]


def test_detect_local_file_sources():
    """Test local file detection from annotations."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "log_file": "production.log",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "local_file" in sources
    assert sources["local_file"]["log_file"] == "production.log"


def test_detect_local_file_sources_alternative_names():
    """Test local file detection with alternative naming."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "log_path": "/var/log/pipeline.log",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "local_file" in sources
    assert sources["local_file"]["log_file"] == "/var/log/pipeline.log"


def test_detect_tracer_web_sources():
    """Test Tracer Web detection from context."""
    state: InvestigationState = {
        "raw_alert": {},
        "context": {
            "tracer_web_run": {
                "trace_id": "a4b56a5c-03c5-438f-96b6-60f8db7c13d5",
                "run_url": "https://staging.tracer.cloud/pipelines/test/batch/a4b56a5c",
            }
        },
    }

    sources = detect_available_sources(state)
    assert "tracer_web" in sources
    assert sources["tracer_web"]["trace_id"] == "a4b56a5c-03c5-438f-96b6-60f8db7c13d5"
    assert sources["tracer_web"]["run_url"] == "https://staging.tracer.cloud/pipelines/test/batch/a4b56a5c"


def test_detect_tracer_web_sources_no_url():
    """Test Tracer Web detection without run_url."""
    state: InvestigationState = {
        "raw_alert": {},
        "context": {
            "tracer_web_run": {
                "trace_id": "a4b56a5c-03c5-438f-96b6-60f8db7c13d5",
            }
        },
    }

    sources = detect_available_sources(state)
    assert "tracer_web" in sources
    assert sources["tracer_web"]["trace_id"] == "a4b56a5c-03c5-438f-96b6-60f8db7c13d5"
    assert "run_url" not in sources["tracer_web"]


def test_detect_multiple_sources():
    """Test detection of multiple sources simultaneously."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
                "cloudwatch_log_stream": "job-12345/container-name/abc123",
                "s3_bucket": "my-data-bucket",
                "s3_prefix": "raw/events/",
                "log_file": "production.log",
            }
        },
        "context": {
            "tracer_web_run": {
                "trace_id": "a4b56a5c-03c5-438f-96b6-60f8db7c13d5",
            }
        },
    }

    sources = detect_available_sources(state)
    assert "cloudwatch" in sources
    assert "s3" in sources
    assert "local_file" in sources
    assert "tracer_web" in sources


def test_detect_no_sources():
    """Test detection with no available sources."""
    state: InvestigationState = {
        "raw_alert": {},
        "context": {},
    }

    sources = detect_available_sources(state)
    assert sources == {}


def test_detect_cloudwatch_requires_both_group_and_stream():
    """Test that CloudWatch detection requires both log_group and log_stream."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
            }
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "cloudwatch" not in sources


def test_detect_top_level_annotations():
    """Test detection from top-level raw_alert fields."""
    state: InvestigationState = {
        "raw_alert": {
            "cloudwatch_log_group": "/aws/batch/job",
            "cloudwatch_log_stream": "job-12345/container-name/abc123",
        },
        "context": {},
    }

    sources = detect_available_sources(state)
    assert "cloudwatch" in sources
    assert sources["cloudwatch"]["log_group"] == "/aws/batch/job"
    assert sources["cloudwatch"]["log_stream"] == "job-12345/container-name/abc123"
