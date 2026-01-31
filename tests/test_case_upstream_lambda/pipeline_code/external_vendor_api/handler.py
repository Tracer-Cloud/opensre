"""
Mock External API Lambda for ECS Fargate Airflow Test Case.

This simulates an external data provider that the Lambda ingester calls.
The API can be configured to return data with schema changes to simulate
upstream API changes that cause downstream failures.

Environment Variables:
- INJECT_SCHEMA_CHANGE: Set to "true" to omit customer_id field

Endpoints:
- GET /health - Health check
- GET /data - Returns order data
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
    """Return order data. If inject_schema_change is True, omits customer_id field."""
    timestamp = datetime.utcnow().isoformat()

    base_data = [
        {"order_id": "ORD-001", "amount": 99.99, "timestamp": timestamp},
        {"order_id": "ORD-002", "amount": 149.50, "timestamp": timestamp},
        {"order_id": "ORD-003", "amount": 75.00, "timestamp": timestamp},
    ]

    if _config["inject_schema_change"]:
        # Schema violation: missing customer_id
        return _response(
            200,
            {
                "data": base_data,
                "meta": {
                    "schema_version": "2.0",
                    "record_count": len(base_data),
                    "timestamp": timestamp,
                    "note": "BREAKING: customer_id field removed in v2.0",
                },
            },
        )

    # Normal response with customer_id
    for i, record in enumerate(base_data):
        record["customer_id"] = f"CUST-{i + 1:03d}"

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
