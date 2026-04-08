#!/usr/bin/env python3
"""Validate Mintlify navigation references for OpenSRE docs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


DOCS_DIR = Path(__file__).resolve().parents[1]
DOCS_JSON_PATH = DOCS_DIR / "docs.json"


def collect_pages(items: list[Any]) -> list[str]:
    pages: list[str] = []
    for item in items:
        if isinstance(item, str):
            pages.append(item)
            continue
        if isinstance(item, dict):
            nested = item.get("pages")
            if isinstance(nested, list):
                pages.extend(collect_pages(nested))
    return pages


def main() -> int:
    if not DOCS_JSON_PATH.exists():
        print(f"ERROR: Missing docs config: {DOCS_JSON_PATH}")
        return 1

    with DOCS_JSON_PATH.open("r", encoding="utf-8") as handle:
        docs_config = json.load(handle)

    navigation = docs_config.get("navigation", {})
    groups = navigation.get("groups", [])
    page_ids = collect_pages(groups)

    missing_paths: list[str] = []
    for page_id in page_ids:
        candidate = DOCS_DIR / f"{page_id}.mdx"
        if not candidate.exists():
            missing_paths.append(f"{page_id} -> {candidate.relative_to(DOCS_DIR)}")

    unique_count = len(set(page_ids))
    print(f"Checked {len(page_ids)} navigation entries ({unique_count} unique pages).")

    if missing_paths:
        print("\nMissing pages referenced by docs.json navigation:")
        for item in sorted(set(missing_paths)):
            print(f"- {item}")
        return 1

    print("Navigation check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
