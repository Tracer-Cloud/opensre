"""
Mock External API for MWAA Test Case.

This simulates an external data provider that the Lambda ingester calls.
The API can be configured to return data with schema changes to simulate
upstream API changes that cause downstream failures.

Environment Variables:
- INJECT_SCHEMA_CHANGE: Set to "true" to omit customer_id field
- PORT: Port to listen on (default 8080)

Endpoints:
- GET /health - Health check
- GET /data - Returns order data
- POST /config - Update schema change injection setting
"""

import os
from datetime import datetime
from flask import Flask, jsonify, request

app = Flask(__name__)

# Global configuration
config = {
    "inject_schema_change": os.getenv("INJECT_SCHEMA_CHANGE", "false").lower() == "true",
}


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "config": config,
    })


@app.route("/data", methods=["GET"])
def get_data():
    """
    Return order data.

    If inject_schema_change is True, returns data without customer_id field.
    """
    timestamp = datetime.utcnow().isoformat()

    base_data = [
        {
            "order_id": "ORD-001",
            "amount": 99.99,
            "timestamp": timestamp,
        },
        {
            "order_id": "ORD-002",
            "amount": 149.50,
            "timestamp": timestamp,
        },
        {
            "order_id": "ORD-003",
            "amount": 75.00,
            "timestamp": timestamp,
        },
    ]

    if config["inject_schema_change"]:
        # Return data WITHOUT customer_id (schema violation)
        app.logger.info("Returning data with schema change (missing customer_id)")
        return jsonify({
            "data": base_data,
            "meta": {
                "schema_version": "2.0",  # Simulates a breaking change
                "record_count": len(base_data),
                "timestamp": timestamp,
                "note": "BREAKING: customer_id field removed in v2.0",
            },
        })

    # Add customer_id to all records
    for i, record in enumerate(base_data):
        record["customer_id"] = f"CUST-{i + 1:03d}"

    return jsonify({
        "data": base_data,
        "meta": {
            "schema_version": "1.0",
            "record_count": len(base_data),
            "timestamp": timestamp,
        },
    })


@app.route("/config", methods=["POST"])
def update_config():
    """Update API configuration."""
    data = request.get_json() or {}

    if "inject_schema_change" in data:
        config["inject_schema_change"] = bool(data["inject_schema_change"])
        app.logger.info(f"Updated inject_schema_change to {config['inject_schema_change']}")

    return jsonify({
        "status": "updated",
        "config": config,
    })


@app.route("/config", methods=["GET"])
def get_config():
    """Get current API configuration."""
    return jsonify(config)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
