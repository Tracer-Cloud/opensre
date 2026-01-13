"""Tests for mock services."""

import pytest

from src.mocks.s3 import get_s3_client, MockS3Client
from src.mocks.nextflow import get_nextflow_client, MockNextflowClient
from src.mocks.warehouse import get_warehouse_client, MockWarehouseClient


class TestMockS3:
    """Test mock S3 client."""
    
    def test_list_raw_files(self):
        """Test listing files in raw bucket."""
        client = get_s3_client()
        files = client.list_objects("tracer-raw-data", "events/2026-01-13/")
        
        assert len(files) == 1
        assert "events_raw.parquet" in files[0]["key"]
    
    def test_list_processed_files(self):
        """Test listing files in processed bucket."""
        client = get_s3_client()
        files = client.list_objects("tracer-processed-data", "events/2026-01-13/")
        
        # Should have the processed file but NOT _SUCCESS marker
        assert len(files) == 1
        assert "events_processed.parquet" in files[0]["key"]
    
    def test_success_marker_missing(self):
        """Test that _SUCCESS marker is missing (the bug!)."""
        client = get_s3_client()
        exists = client.object_exists("tracer-processed-data", "events/2026-01-13/_SUCCESS")
        
        assert exists is False
    
    def test_object_metadata(self):
        """Test getting object metadata."""
        client = get_s3_client()
        meta = client.get_object_metadata("tracer-raw-data", "events/2026-01-13/events_raw.parquet")
        
        assert meta is not None
        assert meta["size"] > 0


class TestMockNextflow:
    """Test mock Nextflow client."""
    
    def test_get_latest_run(self):
        """Test getting the latest pipeline run."""
        client = get_nextflow_client()
        run = client.get_latest_run("events-etl")
        
        assert run is not None
        assert run["status"] == "FAILED"
    
    def test_get_steps(self):
        """Test getting pipeline steps."""
        client = get_nextflow_client()
        run = client.get_latest_run("events-etl")
        steps = client.get_steps(run["run_id"])
        
        assert len(steps) == 3
        
        # Validate, transform should be completed
        completed = [s for s in steps if s["status"] == "COMPLETED"]
        assert len(completed) == 2
        
        # Finalize should be failed
        failed = [s for s in steps if s["status"] == "FAILED"]
        assert len(failed) == 1
        assert failed[0]["step_name"] == "finalize"
    
    def test_get_step_logs(self):
        """Test getting step logs."""
        client = get_nextflow_client()
        run = client.get_latest_run("events-etl")
        logs = client.get_step_logs(run["run_id"], "finalize")
        
        assert logs is not None
        assert "S3 PutObject failed" in logs
        assert "AccessDenied" in logs


class TestMockWarehouse:
    """Test mock warehouse client."""
    
    def test_table_freshness(self):
        """Test getting table freshness info."""
        client = get_warehouse_client()
        info = client.get_table_freshness("events_fact")
        
        assert info is not None
        assert info["is_stale"] is True
        assert info["hours_since_update"] > 24  # Very stale
    
    def test_loader_status(self):
        """Test getting loader status."""
        client = get_warehouse_client()
        loaders = client.list_loaders_for_table("events_fact")
        
        assert len(loaders) == 1
        assert loaders[0]["status"] == "WAITING"
        assert "_SUCCESS" in loaders[0]["waiting_for"]

