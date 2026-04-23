"""Hugging Face Hub helpers: stream ``query_alerts`` JSON and cache telemetry CSV trees."""

from __future__ import annotations

import copy
import json
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.integrations.opensre.constants import OPENSRE_HF_DATASET_ID

_MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s*(\d{4})\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _hub_import_error(name: str) -> ImportError:
    return ImportError(
        f"{name} is required for Hugging Face OpenSRE helpers. "
        "Install with: pip install 'opensre[opensre-hub]'"
    )


def hub_repo_prefix_from_pipeline(pipeline_name: str) -> str:
    """Map alert ``pipeline_name`` to a dataset path prefix (e.g. ``market/cloudbed-1`` → ``Market/cloudbed-1``)."""
    parts = [p for p in pipeline_name.strip("/").split("/") if p]
    if not parts:
        return ""
    parts[0] = parts[0][:1].upper() + parts[0][1:]
    return "/".join(parts)


def telemetry_date_folder_from_text(*texts: str) -> str | None:
    """Return ``YYYY_MM_DD`` for the first calendar date found across ``texts``."""
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return None
    m = _MONTH_RE.search(blob)
    if m:
        month = _MONTHS[m.group(1).lower()]
        day = int(m.group(2))
        year = int(m.group(3))
        return f"{year:04d}_{month:02d}_{day:02d}"
    m2 = _ISO_DATE_RE.search(blob)
    if m2:
        y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        return f"{y:04d}_{mo:02d}_{d:02d}"
    return None


def infer_opensre_telemetry_relative(raw_alert: dict[str, Any]) -> str | None:
    """Derive ``<Prefix>/telemetry/YYYY_MM_DD`` when annotations omit ``*_telemetry_relative``."""
    labels_raw = raw_alert.get("commonLabels")
    labels: dict[str, Any] = labels_raw if isinstance(labels_raw, dict) else {}
    pipeline = str(labels.get("pipeline_name") or raw_alert.get("pipeline_name") or "").strip()
    if not pipeline:
        return None
    prefix = hub_repo_prefix_from_pipeline(pipeline)
    ann = raw_alert.get("commonAnnotations") or {}
    if not isinstance(ann, dict):
        ann = {}
    texts = [
        str(raw_alert.get("message") or ""),
        str(raw_alert.get("text") or ""),
        str(ann.get("summary") or ""),
        str(ann.get("query") or ""),
    ]
    day = telemetry_date_folder_from_text(*texts)
    if not day:
        return None
    return f"{prefix}/telemetry/{day}"


def strip_scoring_points_from_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Drop ``scoring_points`` from annotations so agent runs do not see rubric text."""
    out = copy.deepcopy(alert)
    for key in ("commonAnnotations", "annotations"):
        nested = out.get(key)
        if isinstance(nested, dict) and "scoring_points" in nested:
            out[key] = {k: v for k, v in nested.items() if k != "scoring_points"}
    return out


def extract_openrca_scoring_points(alert: dict[str, Any]) -> str:
    """
    Collect ``scoring_points`` from ``commonAnnotations`` and ``annotations``.

    Used for offline LLM judges; the investigation agent should not see this text
    (use :func:`strip_scoring_points_from_alert` on the alert passed into the graph).
    """
    chunks: list[str] = []
    for block_name in ("commonAnnotations", "annotations"):
        block = alert.get(block_name)
        if not isinstance(block, dict):
            continue
        raw = block.get("scoring_points")
        if raw is None or raw == "":
            continue
        if isinstance(raw, str):
            text = raw.strip()
        else:
            try:
                text = json.dumps(raw, indent=2)
            except (TypeError, ValueError):
                text = str(raw)
        chunks.append(f"## {block_name}.scoring_points\n{text}")
    return "\n\n".join(chunks).strip()


def stream_opensre_query_alerts(
    *,
    query_alerts_prefix: str,
    dataset_id: str | None = None,
    revision: str | None = None,
    strip_scoring_points: bool = False,
) -> Iterator[dict[str, Any]]:
    """Yield alert dicts from ``<prefix>/*.json`` using Hugging Face ``datasets`` streaming.

    By default **does not** strip ``scoring_points`` so saved alerts work with
    ``opensre investigate --evaluate``. Pass ``strip_scoring_points=True`` for a stream
    with rubric removed (e.g. publishing blind fixtures). Investigations still strip
    rubric from the in-graph ``raw_alert`` when ``--evaluate`` is off — see
    :func:`app.state.factory.make_initial_state`.
    """
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise _hub_import_error("datasets") from e

    repo = (dataset_id or OPENSRE_HF_DATASET_ID).strip()
    rev = (revision or os.environ.get("OPENSRE_HF_REVISION") or "main").strip()
    prefix = query_alerts_prefix.strip().strip("/")
    url = f"hf://datasets/{repo}@{rev}/{prefix}/*.json"
    ds = load_dataset("json", data_files=url, streaming=True, split="train")
    for row in ds:
        row_dict = dict(row)
        if strip_scoring_points:
            row_dict = strip_scoring_points_from_alert(row_dict)
        yield row_dict


def default_hf_cache_dir() -> Path:
    root = os.environ.get("OPENSRE_HF_CACHE", "").strip()
    if root:
        return Path(root).expanduser()
    return Path.home() / ".cache" / "opensre" / "hf"


def materialize_opensre_telemetry_from_hub(
    *,
    dataset_id: str,
    telemetry_relative: str,
    revision: str | None = None,
    cache_dir: Path | None = None,
) -> Path:
    """Download only ``telemetry_relative/**`` from the dataset repo into a persistent cache."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError as e:
        raise _hub_import_error("huggingface_hub") from e

    rel = telemetry_relative.strip().strip("/")
    if not rel:
        raise ValueError("telemetry_relative must be non-empty")
    rev = (revision or os.environ.get("OPENSRE_HF_REVISION") or "main").strip()
    base = cache_dir or default_hf_cache_dir()
    dest = (base / dataset_id.replace("/", "__") / rev).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    pattern = f"{rel}/**"
    snapshot_download(
        repo_id=dataset_id,
        repo_type="dataset",
        revision=rev,
        local_dir=str(dest),
        allow_patterns=[pattern],
    )
    out = (dest / rel).resolve()
    if not out.is_dir():
        raise FileNotFoundError(f"Telemetry path missing after Hub download: {out}")
    return out
