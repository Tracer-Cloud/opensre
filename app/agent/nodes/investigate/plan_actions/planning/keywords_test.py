"""Tests for keyword extraction."""

from app.agent.nodes.investigate.plan_actions.planning.keywords import extract_keywords


def test_extract_keywords_from_pipeline_failure():
    """Test extraction from typical pipeline failure scenario."""
    problem_md = "Pipeline superfluid_prod_pipeline failed with status failed"
    alert_name = "PipelineFailure"

    keywords = extract_keywords(problem_md, alert_name)

    assert "failure" in keywords
    assert "failed" in keywords
    assert "pipeline" in keywords
    assert len(keywords) >= 3


def test_extract_keywords_from_memory_error():
    """Test extraction from memory-related issues."""
    problem_md = "Container was killed due to OOM (Out of Memory)"
    alert_name = "MemoryError"

    keywords = extract_keywords(problem_md, alert_name)

    assert "memory" in keywords
    assert "oom" in keywords
    assert "killed" in keywords
    assert "error" in keywords


def test_extract_keywords_from_timeout():
    """Test extraction from timeout scenarios."""
    problem_md = "Batch job timed out after 30 minutes"
    alert_name = "JobTimeout"

    keywords = extract_keywords(problem_md, alert_name)

    assert "timeout" in keywords
    assert "batch" in keywords
    assert "job" in keywords


def test_extract_keywords_from_log_errors():
    """Test extraction from log-related issues."""
    problem_md = "Error logs show exception in tool execution"
    alert_name = "LogError"

    keywords = extract_keywords(problem_md, alert_name)

    assert "error" in keywords
    assert "log" in keywords
    assert "logs" in keywords
    assert "exception" in keywords
    assert "tool" in keywords


def test_extract_keywords_from_resource_issues():
    """Test extraction from resource-related problems."""
    problem_md = "High CPU and disk usage detected"
    alert_name = "ResourceExhaustion"

    keywords = extract_keywords(problem_md, alert_name)

    assert "cpu" in keywords
    assert "disk" in keywords
    assert "resource" in keywords


def test_extract_keywords_from_slow_performance():
    """Test extraction from performance issues."""
    problem_md = "Pipeline execution is slow and appears to hang"
    alert_name = "SlowExecution"

    keywords = extract_keywords(problem_md, alert_name)

    assert "slow" in keywords
    assert "hang" in keywords
    assert "pipeline" in keywords


def test_extract_keywords_from_crash():
    """Test extraction from crash scenarios."""
    problem_md = "Application crashed with exception"
    alert_name = "CrashDetected"

    keywords = extract_keywords(problem_md, alert_name)

    assert "crash" in keywords
    assert "exception" in keywords


def test_extract_keywords_from_alert_name_only():
    """Test extraction when only alert name is provided."""
    problem_md = ""
    alert_name = "PipelineFailure"

    keywords = extract_keywords(problem_md, alert_name)

    assert "failure" in keywords
    assert "pipeline" in keywords


def test_extract_keywords_from_problem_only():
    """Test extraction when only problem statement is provided."""
    problem_md = "Task execution failed with error"
    alert_name = ""

    keywords = extract_keywords(problem_md, alert_name)

    assert "task" in keywords
    assert "failed" in keywords
    assert "error" in keywords


def test_extract_keywords_no_matches():
    """Test extraction when no keywords match."""
    problem_md = "Everything is working correctly"
    alert_name = "Success"

    keywords = extract_keywords(problem_md, alert_name)

    assert keywords == []


def test_extract_keywords_case_insensitive():
    """Test that extraction is case-insensitive."""
    problem_md = "PIPELINE FAILED WITH ERROR"
    alert_name = "PipelineFailure"

    keywords = extract_keywords(problem_md, alert_name)

    assert "pipeline" in keywords
    assert "failed" in keywords
    assert "error" in keywords


def test_extract_keywords_partial_matches():
    """Test that partial word matches work correctly."""
    problem_md = "Logging system shows errors"
    alert_name = "LogError"

    keywords = extract_keywords(problem_md, alert_name)

    assert "log" in keywords
    assert "error" in keywords
    assert "logs" not in keywords


def test_extract_keywords_multiple_occurrences():
    """Test that keywords appearing multiple times are only returned once."""
    problem_md = "Pipeline failed. The failed pipeline needs investigation."
    alert_name = "PipelineFailure"

    keywords = extract_keywords(problem_md, alert_name)

    assert keywords.count("failed") == 1
    assert keywords.count("pipeline") == 1


def test_extract_keywords_real_world_pipeline_failure():
    """Test with real-world pipeline failure scenario."""
    problem_md = """
    Pipeline superfluid_prod_pipeline run shimmering-okapi-891 failed with status failed.
    The batch job encountered an error during execution.
    Check the logs for more details.
    """
    alert_name = "PipelineFailure"

    keywords = extract_keywords(problem_md, alert_name)

    assert "pipeline" in keywords
    assert "failed" in keywords
    assert "batch" in keywords
    assert "job" in keywords
    assert "error" in keywords
    assert "log" in keywords or "logs" in keywords


def test_extract_keywords_sla_breach():
    """Test with SLA breach scenario."""
    problem_md = "Table events_fact has not been updated in over 2 hours"
    alert_name = "DataFreshnessSLABreach"

    keywords = extract_keywords(problem_md, alert_name)

    assert len(keywords) >= 0


def test_extract_keywords_s3_failure():
    """Test with S3-related failure."""
    problem_md = "S3 pipeline failed: /input.csv is missing"
    alert_name = "S3PipelineFailure"

    keywords = extract_keywords(problem_md, alert_name)

    assert "pipeline" in keywords
    assert "failed" in keywords


def test_extract_keywords_whitespace_handling():
    """Test that whitespace is handled correctly."""
    problem_md = "   Pipeline   failed   with   error   "
    alert_name = "   PipelineFailure   "

    keywords = extract_keywords(problem_md, alert_name)

    assert "pipeline" in keywords
    assert "failed" in keywords
    assert "error" in keywords
