"""
Mock External API Lambda for ML Feature Engineering Test Cases.

This simulates an external event stream provider that ML pipelines consume.
The API can be configured to return data with schema changes to simulate
upstream API changes that cause downstream ML pipeline failures.

Environment Variables:
- INJECT_SCHEMA_CHANGE: Set to "true" to omit event_id field

Endpoints:
- GET /health - Health check
- GET /data - Returns ML event data (user events with raw features)
- POST /config - Update schema change injection setting
- GET /config - Get current configuration
"""

import json
import os
from datetime import datetime

# Mutable config stored in Lambda memory (resets on cold start)
_config = {"inject_schema_change": os.getenv("INJECT_SCHEMA_CHANGE", "false").lower() == "true"}


def lambda_handler(event, context):
    """
    Lambda handler for API Gateway requests.

    Handles:
    - GET /health
    - GET /data
    - POST /config
    - GET /config
    """
    # Handle API Gateway v2 (HTTP API) and v1 (REST API) formats
    path = event.get("path") or event.get("rawPath", "/")
    method = (
        event.get("httpMethod")
        or event.get("requestContext", {}).get("http", {}).get("method", "GET")
        or event.get("requestContext", {}).get("httpMethod", "GET")
    )

    if path == "/health" and method == "GET":
        return _response(
            200,
            {
                "status": "healthy",
                "timestamp": datetime.utcnow().isoformat(),
                "config": _config,
            },
        )
    elif path == "/data" and method == "GET":
        return _get_data()
    elif path == "/config" and method == "POST":
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        return _update_config(body)
    elif path == "/config" and method == "GET":
        return _response(200, _config)
    else:
        return _response(404, {"error": "Not found", "path": path, "method": method})


def _get_data():
    """Return ML event data. If inject_schema_change is True, omits event_id field."""
    timestamp = datetime.utcnow().isoformat()

    # ML event data format (feature engineering pipeline)
    base_data = [
        {
            "user_id": "user_12345",
            "timestamp": timestamp,
            "event_type": "click",
            "raw_features": {
                "value": 150.0,
                "duration": 45,
                "count": 3,
                "is_weekend": 0,
                "hour": 14,
            },
        },
        {
            "user_id": "user_67890",
            "timestamp": timestamp,
            "event_type": "purchase",
            "raw_features": {
                "value": 299.99,
                "duration": 120,
                "count": 1,
                "is_weekend": 1,
                "hour": 18,
            },
        },
        {
            "user_id": "user_11223",
            "timestamp": timestamp,
            "event_type": "view",
            "raw_features": {
                "value": 0.0,
                "duration": 15,
                "count": 1,
                "is_weekend": 0,
                "hour": 10,
            },
        },
    ]

    if _config["inject_schema_change"]:
        # Schema violation: missing event_id (critical for ML feature deduplication)
        return _response(
            200,
            {
                "data": base_data,
                "meta": {
                    "schema_version": "2.0",
                    "record_count": len(base_data),
                    "timestamp": timestamp,
                    "note": "BREAKING: event_id field removed in v2.0",
                },
            },
        )

    # Normal response with event_id
    for i, record in enumerate(base_data):
        record["event_id"] = f"evt_{i + 1:03d}"

    return _response(
        200,
        {
            "data": base_data,
            "meta": {
                "schema_version": "1.0",
                "record_count": len(base_data),
                "timestamp": timestamp,
            },
        },
    )


def _update_config(body):
    """Update API configuration."""
    if "inject_schema_change" in body:
        _config["inject_schema_change"] = bool(body["inject_schema_change"])

    return _response(200, {"status": "updated", "config": _config})


def _response(status_code, body):
    """Format Lambda response for API Gateway."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
