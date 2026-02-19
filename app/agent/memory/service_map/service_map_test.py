"""Tests for service map builder."""

import json
from datetime import UTC, datetime

from app.agent.memory.service_map import (
    build_service_map,
    get_compact_asset_inventory,
    load_service_map,
    persist_service_map,
)


class TestServiceMap:
    """Test service map building and persistence."""

    def setup_method(self):
        """Clean service map and enable for tests."""
        import app.agent.memory.service_map.config as config
        from app.agent.memory.io import get_memories_dir

        config.SERVICE_MAP_ENABLED = True
        # Tests need synchronous writes to assert file state immediately after persist
        config.SERVICE_MAP_WRITE_ASYNC = False

        service_map_path = get_memories_dir() / "service_map.json"
        if service_map_path.exists():
            service_map_path.unlink()

    def test_build_service_map_with_lambda_and_s3(self):
        """Build service map with Lambda and S3 assets."""
        evidence = {
            "lambda_function": {
                "function_name": "test-lambda",
                "runtime": "python3.9",
            },
            "s3_object": {
                "found": True,
                "bucket": "test-bucket",
                "key": "data/input.json",
                "metadata": {"source": "test-lambda"},
            },
        }

        raw_alert = {
            "annotations": {
                "function_name": "test-lambda",
                "landing_bucket": "test-bucket",
            }
        }

        service_map = build_service_map(
            evidence=evidence,
            raw_alert=raw_alert,
            context={},
            pipeline_name="test_pipeline",
            alert_name="test_alert",
        )

        assert service_map["enabled"] is True
        assert len(service_map["assets"]) >= 2  # Lambda + S3
        assert len(service_map["edges"]) >= 1  # Lambda -> S3

        # Check Lambda asset
        lambda_assets = [a for a in service_map["assets"] if a["type"] == "lambda"]
        assert len(lambda_assets) == 1
        assert lambda_assets[0]["name"] == "test-lambda"
        assert lambda_assets[0]["confidence"] == 1.0
        assert lambda_assets[0]["verification_status"] == "verified"

        # Check S3 asset
        s3_assets = [a for a in service_map["assets"] if a["type"] == "s3_bucket"]
        assert len(s3_assets) == 1
        assert s3_assets[0]["name"] == "test-bucket"

        # Check edge
        edges = service_map["edges"]
        assert any(
            e["from_asset"] == lambda_assets[0]["id"]
            and e["to_asset"] == s3_assets[0]["id"]
            and e["type"] == "writes_to"
            for e in edges
        )

    def test_build_service_map_with_external_api(self):
        """Build service map with external API → Lambda edge."""
        evidence = {
            "lambda_function": {
                "function_name": "ingestion-lambda",
                "runtime": "python3.9",
            },
            "s3_audit_payload": {
                "found": True,
                "bucket": "audit-bucket",
                "key": "audit/payload.json",
                "content": json.dumps({"external_api_url": "https://api.vendor.com"}),
            },
        }

        raw_alert = {
            "annotations": {
                "function_name": "ingestion-lambda",
                "trigger_lambda": "ingestion-lambda",
            }
        }

        service_map = build_service_map(
            evidence=evidence,
            raw_alert=raw_alert,
            context={},
            pipeline_name="test_pipeline",
            alert_name="test_alert",
        )

        # Check external API asset (should be created)
        external_api_assets = [a for a in service_map["assets"] if a["type"] == "external_api"]
        assert len(external_api_assets) == 1
        assert external_api_assets[0]["confidence"] == 0.8
        assert external_api_assets[0]["verification_status"] == "verified"

        # Check edge: External API -> Lambda
        lambda_assets = [a for a in service_map["assets"] if a["type"] == "lambda"]
        edges = service_map["edges"]
        assert any(
            e["from_asset"] == external_api_assets[0]["id"]
            and e["to_asset"] == lambda_assets[0]["id"]
            and e["type"] == "triggers"
            for e in edges
        )

    def test_infer_tentative_assets_from_alert(self):
        """Infer tentative S3 asset when alert mentions 'Lambda timeout writing to S3'."""
        evidence = {
            "lambda_function": {
                "function_name": "test-lambda",
                "runtime": "python3.9",
            }
        }

        raw_alert = {
            "annotations": {
                "function_name": "test-lambda",
                "error": "Lambda timeout writing to S3 bucket",
            }
        }

        service_map = build_service_map(
            evidence=evidence,
            raw_alert=raw_alert,
            context={},
            pipeline_name="test_pipeline",
            alert_name="Lambda timeout writing to S3",
        )

        # Check tentative S3 asset
        s3_assets = [a for a in service_map["assets"] if a["type"] == "s3_bucket"]
        assert len(s3_assets) == 1
        assert s3_assets[0]["confidence"] == 0.6
        assert s3_assets[0]["verification_status"] == "needs_verification"
        assert s3_assets[0]["name"] == "tentative_destination"

        # Check tentative edge
        edges = service_map["edges"]
        tentative_edges = [
            e for e in edges if e["verification_status"] == "needs_verification"
        ]
        assert len(tentative_edges) >= 1
        assert tentative_edges[0]["confidence"] == 0.7

    def test_merge_with_existing_map_updates_hotspots(self):
        """Merge new map with existing map updates investigation hotspots."""
        # First investigation
        evidence1 = {
            "lambda_function": {
                "function_name": "test-lambda",
                "runtime": "python3.9",
            }
        }

        raw_alert1 = {
            "annotations": {
                "function_name": "test-lambda",
            }
        }

        service_map1 = build_service_map(
            evidence=evidence1,
            raw_alert=raw_alert1,
            context={},
            pipeline_name="test_pipeline",
            alert_name="alert1",
        )
        persist_service_map(service_map1)

        # Second investigation (same Lambda)
        evidence2 = {
            "lambda_function": {
                "function_name": "test-lambda",
                "runtime": "python3.9",
            }
        }

        raw_alert2 = {
            "annotations": {
                "function_name": "test-lambda",
            }
        }

        service_map2 = build_service_map(
            evidence=evidence2,
            raw_alert=raw_alert2,
            context={},
            pipeline_name="test_pipeline",
            alert_name="alert2",
        )

        # Check Lambda asset investigation count
        lambda_assets = [a for a in service_map2["assets"] if a["type"] == "lambda"]
        assert len(lambda_assets) == 1
        assert lambda_assets[0]["investigation_count"] == 2  # Hotspot!

    def test_history_tracks_changes(self):
        """Service map history tracks asset/edge additions."""
        # First investigation - add Lambda
        evidence1 = {
            "lambda_function": {
                "function_name": "lambda1",
                "runtime": "python3.9",
            }
        }

        raw_alert1 = {"annotations": {"function_name": "lambda1"}}

        service_map1 = build_service_map(
            evidence=evidence1,
            raw_alert=raw_alert1,
            context={},
            pipeline_name="test_pipeline",
            alert_name="alert1",
        )
        persist_service_map(service_map1)

        # Second investigation - add S3
        evidence2 = {
            "lambda_function": {
                "function_name": "lambda1",
                "runtime": "python3.9",
            },
            "s3_object": {
                "found": True,
                "bucket": "test-bucket",
                "key": "data/input.json",
                "metadata": {"source": "lambda1"},
            },
        }

        raw_alert2 = {
            "annotations": {
                "function_name": "lambda1",
                "landing_bucket": "test-bucket",
            }
        }

        service_map2 = build_service_map(
            evidence=evidence2,
            raw_alert=raw_alert2,
            context={},
            pipeline_name="test_pipeline",
            alert_name="alert2",
        )

        # Check history
        history = service_map2["history"]
        assert len(history) > 0

        # Should have asset additions
        asset_additions = [h for h in history if h["change_type"] == "asset_added"]
        assert len(asset_additions) >= 2  # Lambda + S3

    def test_history_retains_last_20_entries(self):
        """Service map history retains only last 20 changes."""
        # Create initial map
        service_map = load_service_map()
        service_map["enabled"] = True

        # Add 25 history entries
        for i in range(25):
            service_map["history"].append(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "change_type": "asset_added",
                    "asset_id": f"test_asset_{i}",
                    "edge_id": None,
                    "details": f"Asset {i}",
                }
            )

        persist_service_map(service_map)

        # Load and verify only last 20 retained
        loaded_map = load_service_map()
        assert len(loaded_map["history"]) == 20
        # Should have entries 5-24 (last 20)
        assert "Asset 24" in loaded_map["history"][-1]["details"]
        assert "Asset 5" in loaded_map["history"][0]["details"]

    def test_tentative_asset_verification(self):
        """Tentative assets become verified when confirmed."""
        # First investigation - create tentative S3 asset
        evidence1 = {
            "lambda_function": {
                "function_name": "test-lambda",
                "runtime": "python3.9",
            }
        }

        raw_alert1 = {
            "annotations": {
                "function_name": "test-lambda",
                "error": "Lambda timeout writing to S3 bucket",
            }
        }

        service_map1 = build_service_map(
            evidence=evidence1,
            raw_alert=raw_alert1,
            context={},
            pipeline_name="test_pipeline",
            alert_name="Lambda timeout writing to S3",
        )
        persist_service_map(service_map1)

        # Check tentative S3 exists
        s3_assets = [a for a in service_map1["assets"] if a["type"] == "s3_bucket"]
        assert len(s3_assets) == 1
        assert s3_assets[0]["verification_status"] == "needs_verification"

        # Second investigation - verify S3 asset
        evidence2 = {
            "lambda_function": {
                "function_name": "test-lambda",
                "runtime": "python3.9",
            },
            "s3_object": {
                "found": True,
                "bucket": "real-bucket",
                "key": "data/input.json",
                "metadata": {"source": "test-lambda"},
            },
        }

        raw_alert2 = {
            "annotations": {
                "function_name": "test-lambda",
                "landing_bucket": "real-bucket",
            }
        }

        service_map2 = build_service_map(
            evidence=evidence2,
            raw_alert=raw_alert2,
            context={},
            pipeline_name="test_pipeline",
            alert_name="alert2",
        )

        # Check verified S3 asset added
        verified_s3_assets = [
            a
            for a in service_map2["assets"]
            if a["type"] == "s3_bucket" and a["verification_status"] == "verified"
        ]
        assert len(verified_s3_assets) >= 1

    def test_get_compact_asset_inventory(self):
        """Get compact asset inventory limits to N assets."""
        service_map = {
            "enabled": True,
            "last_updated": datetime.now(UTC).isoformat(),
            "assets": [
                {
                    "id": "lambda:test1",
                    "type": "lambda",
                    "name": "test1",
                    "investigation_count": 5,
                    "confidence": 1.0,
                    "verification_status": "verified",
                },
                {
                    "id": "lambda:test2",
                    "type": "lambda",
                    "name": "test2",
                    "investigation_count": 2,
                    "confidence": 1.0,
                    "verification_status": "verified",
                },
                {
                    "id": "s3_bucket:bucket1",
                    "type": "s3_bucket",
                    "name": "bucket1",
                    "investigation_count": 1,
                    "confidence": 0.6,
                    "verification_status": "needs_verification",
                },
            ],
            "edges": [],
            "history": [],
        }

        inventory = get_compact_asset_inventory(service_map, limit=2)

        # Should prioritize by investigation count (hotspots first)
        assert "test1" in inventory  # 5x investigations
        assert "test2" in inventory  # 2x investigations
        assert "investigated 5x" in inventory
        assert "+1 more assets" in inventory  # Tentative asset is cut off

        # Test with higher limit to see tentative marker
        inventory_full = get_compact_asset_inventory(service_map, limit=10)
        assert "?" in inventory_full  # Tentative marker shown when included

    def test_empty_state_when_disabled(self):
        """Service map returns empty state when disabled."""
        import app.agent.memory.service_map.config as service_map_config

        # Disable service map
        original_value = service_map_config.SERVICE_MAP_ENABLED
        service_map_config.SERVICE_MAP_ENABLED = False

        try:
            service_map = build_service_map(
                evidence={},
                raw_alert={},
                context={},
                pipeline_name="test_pipeline",
                alert_name="test_alert",
            )

            assert service_map["enabled"] is False
            assert service_map["assets"] == []
            assert service_map["edges"] == []
            assert service_map["history"] == []
        finally:
            # Restore original value
            service_map_config.SERVICE_MAP_ENABLED = original_value

    def test_persist_and_load_service_map(self):
        """Persist and load service map from disk."""
        service_map = {
            "enabled": True,
            "last_updated": datetime.now(UTC).isoformat(),
            "assets": [
                {
                    "id": "lambda:test",
                    "type": "lambda",
                    "name": "test",
                    "investigation_count": 1,
                    "confidence": 1.0,
                    "verification_status": "verified",
                }
            ],
            "edges": [
                {
                    "from_asset": "lambda:test",
                    "to_asset": "s3_bucket:bucket",
                    "type": "writes_to",
                    "confidence": 0.9,
                }
            ],
            "history": [],
        }

        # Persist
        path = persist_service_map(service_map)
        assert path.exists()

        # Load
        loaded_map = load_service_map()
        assert loaded_map["enabled"] is True
        assert len(loaded_map["assets"]) == 1
        assert len(loaded_map["edges"]) == 1
        assert loaded_map["assets"][0]["name"] == "test"

    def test_ecs_and_pipeline_assets(self):
        """Build service map with ECS and pipeline assets."""
        evidence = {}

        raw_alert = {
            "annotations": {
                "ecs_cluster": "test-cluster",
                "prefect_flow": "test-flow",
                "dag_id": "test_pipeline",
            }
        }

        service_map = build_service_map(
            evidence=evidence,
            raw_alert=raw_alert,
            context={},
            pipeline_name="test_pipeline",
            alert_name="test_alert",
        )

        # Check ECS asset
        ecs_assets = [a for a in service_map["assets"] if a["type"] == "ecs_cluster"]
        assert len(ecs_assets) == 1
        assert ecs_assets[0]["name"] == "test-cluster"

        # Check pipeline asset
        pipeline_assets = [a for a in service_map["assets"] if a["type"] == "pipeline"]
        assert len(pipeline_assets) == 1

        # Check edge: Pipeline -> ECS
        edges = service_map["edges"]
        assert any(
            e["from_asset"] == pipeline_assets[0]["id"]
            and e["to_asset"] == ecs_assets[0]["id"]
            and e["type"] == "runs_on"
            for e in edges
        )
