import json
import os
import sys


def main():
    if os.getenv("INJECT_ERROR", "").lower() == "true":
        raise ValueError("Injected ETL failure: corrupted data detected")

    raw = os.environ.get("INPUT_DATA", "{}")
    data = json.loads(raw)

    for field in ("id", "name", "value"):
        if field not in data:
            raise KeyError(f"Missing required field: {field}")

    result = {
        "id": data["id"],
        "name": data["name"].upper(),
        "value": data["value"] * 2,
        "status": "processed",
    }
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"PIPELINE_ERROR: {e}", file=sys.stderr)
        sys.exit(1)
