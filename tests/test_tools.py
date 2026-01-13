"""Tests for LangChain tools."""

import pytest

from src.tools.s3_tools import list_s3_files, check_success_marker
from src.tools.nextflow_tools import get_pipeline_run, get_step_status, get_step_logs
from src.tools.warehouse_tools import get_table_freshness, get_loader_status


class TestS3Tools:
    """Test S3 tools."""
    
    def test_list_s3_files_raw(self):
        """Test listing raw files."""
        result = list_s3_files.invoke({
            "bucket": "tracer-raw-data",
            "prefix": "events/2026-01-13/"
        })
        
        assert result["count"] == 1
        assert "Found 1 file" in result["message"]
    
    def test_list_s3_files_processed(self):
        """Test listing processed files."""
        result = list_s3_files.invoke({
            "bucket": "tracer-processed-data",
            "prefix": "events/2026-01-13/"
        })
        
        assert result["count"] == 1
    
    def test_check_success_marker_missing(self):
        """Test that success marker is correctly detected as missing."""
        result = check_success_marker.invoke({
            "bucket": "tracer-processed-data",
            "prefix": "events/2026-01-13/"
        })
        
        assert result["success_marker_exists"] is False
        assert "MISSING" in result["message"]
    
    def test_list_s3_files_empty(self):
        """Test listing files in non-existent location."""
        result = list_s3_files.invoke({
            "bucket": "tracer-raw-data",
            "prefix": "nonexistent/"
        })
        
        assert result["count"] == 0


class TestNextflowTools:
    """Test Nextflow tools."""
    
    def test_get_pipeline_run(self):
        """Test getting pipeline run info."""
        result = get_pipeline_run.invoke({"pipeline_id": "events-etl"})
        
        assert result["found"] is True
        assert result["run"]["status"] == "FAILED"
    
    def test_get_step_status(self):
        """Test getting step status."""
        run_result = get_pipeline_run.invoke({"pipeline_id": "events-etl"})
        run_id = run_result["run"]["run_id"]
        
        result = get_step_status.invoke({"run_id": run_id})
        
        assert result["found"] is True
        assert result["summary"]["completed"] == 2
        assert result["summary"]["failed"] == 1
        assert result["failed_steps"][0]["name"] == "finalize"
    
    def test_get_step_logs(self):
        """Test getting step logs."""
        run_result = get_pipeline_run.invoke({"pipeline_id": "events-etl"})
        run_id = run_result["run"]["run_id"]
        
        result = get_step_logs.invoke({"run_id": run_id, "step_name": "finalize"})
        
        assert result["found"] is True
        assert "AccessDenied" in result["logs"]


class TestWarehouseTools:
    """Test warehouse tools."""
    
    def test_get_table_freshness(self):
        """Test getting table freshness."""
        result = get_table_freshness.invoke({"table_name": "events_fact"})
        
        assert result["found"] is True
        assert result["freshness"]["is_stale"] is True
        assert "STALE" in result["message"]
    
    def test_get_loader_status(self):
        """Test getting loader status."""
        result = get_loader_status.invoke({"table_name": "events_fact"})
        
        assert result["found"] is True
        assert result["summary"]["waiting"] == 1
        assert "_SUCCESS" in result["waiting_details"][0]["waiting_for"]

