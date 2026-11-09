"""Create demo-only credentials or switch the official paymentFailure flag."""

import argparse
import json
import os
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("init", "fail", "recover"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.mode == "init":
        env = root / ".env"
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(f"COLLECTOR_PASSWORD={secrets.token_hex(24)}\n")
            output.write(f"OPENSRE_TELEMETRY_CLICKHOUSE_PASSWORD={secrets.token_hex(24)}\n")
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    flags = {
        "$schema": "https://flagd.dev/schema/v0/flags.json",
        "flags": {
            "paymentFailure": {
                "state": "ENABLED",
                "variants": {"off": 0, "on": 1},
                "defaultVariant": "on" if args.mode == "fail" else "off",
            },
            "emitRawPii": {
                "state": "ENABLED",
                "variants": {"off": False},
                "defaultVariant": "off",
            },
        },
    }
    # Preserve the inode: Docker Desktop bind mounts can retain stale content after rename.
    (runtime / "flags.json").write_text(json.dumps(flags), encoding="utf-8")
    print(f"Demo flags: {'failure' if args.mode == 'fail' else 'healthy'}")


if __name__ == "__main__":
    main()
