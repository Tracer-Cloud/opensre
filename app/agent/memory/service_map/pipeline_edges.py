"""Pipeline-to-pipeline dependency edge inference.

Infers 'feeds_into' edges between pipelines that share storage assets (S3 buckets).
These edges accumulate across investigations: the first time two pipelines share a
bucket, confidence is 0.6 (inferred). Each re-confirmation bumps confidence by 0.15
until it reaches 0.9 (verified).
"""

from __future__ import annotations

from datetime import UTC, datetime

from .identifiers import generate_asset_id
from .types import Edge, ServiceMap

_FEEDS_INTO = "feeds_into"

# Asset types that indicate a pipeline is upstream (produces data)
_UPSTREAM_MARKERS = {"lambda", "external_api", "api_gateway"}

# Asset types that indicate a pipeline is downstream (consumes data)
_DOWNSTREAM_MARKERS = {"ecs_cluster", "batch_queue", "ecs_service"}

_CONFIDENCE_INITIAL = 0.6
_CONFIDENCE_BUMP = 0.15
_CONFIDENCE_MAX = 0.9


def infer_feeds_into_edges(service_map: ServiceMap) -> list[Edge]:
    """Infer pipeline-to-pipeline 'feeds_into' edges from shared S3 bucket assets.

    Examines every S3 bucket asset whose pipeline_context contains two or more
    pipeline names. Uses existing 'writes_to' edges to determine direction;
    falls back to asset-type heuristics when edge evidence is absent.

    Returns edges ready to be merged into the service map. For edges that already
    exist in the map, returns an updated copy with bumped confidence and refreshed
    last_seen. For genuinely new edges, returns them at the initial confidence.
    """
    now = datetime.now(UTC).isoformat()

    existing_by_key: dict[tuple[str, str], Edge] = {
        (e["from_asset"], e["to_asset"]): e
        for e in service_map.get("edges", [])
        if e.get("type") == _FEEDS_INTO
    }

    result: list[Edge] = []
    seen_pairs: set[tuple[str, str]] = set()

    for asset in service_map.get("assets", []):
        if asset.get("type") not in ("s3_bucket",):
            continue

        pipeline_context = asset.get("pipeline_context", [])
        if len(pipeline_context) < 2:
            continue

        upstream, downstream = _determine_direction(
            pipeline_context, asset["id"], service_map
        )
        if not upstream or not downstream or upstream == downstream:
            continue

        from_id = generate_asset_id("pipeline", upstream)
        to_id = generate_asset_id("pipeline", downstream)
        pair = (from_id, to_id)

        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        existing = existing_by_key.get(pair)
        if existing:
            updated = dict(existing)
            old_confidence = float(existing.get("confidence") or _CONFIDENCE_INITIAL)
            new_confidence = min(_CONFIDENCE_MAX, old_confidence + _CONFIDENCE_BUMP)
            updated["confidence"] = new_confidence
            updated["last_seen"] = now
            if new_confidence >= _CONFIDENCE_MAX:
                updated["verification_status"] = "verified"
            result.append(updated)  # type: ignore[arg-type]
        else:
            result.append({
                "from_asset": from_id,
                "to_asset": to_id,
                "type": _FEEDS_INTO,
                "confidence": _CONFIDENCE_INITIAL,
                "verification_status": "inferred",
                "evidence": f"shared_s3_asset:{asset['id']}",
                "first_seen": now,
                "last_seen": now,
            })

    return result


def _determine_direction(
    pipelines: list[str],
    shared_asset_id: str,
    service_map: ServiceMap,
) -> tuple[str | None, str | None]:
    """Return (upstream_pipeline, downstream_pipeline) for a shared asset.

    Priority:
    1. Existing 'writes_to' edge: the pipeline whose Lambda/asset writes to the
       shared bucket is upstream.
    2. Asset-type heuristics: Lambda/API pipelines are upstream; ECS/Batch are
       downstream.
    3. Alphabetical fallback for determinism.
    """
    # Build a set of asset types associated with each pipeline
    pipeline_asset_types: dict[str, set[str]] = {p: set() for p in pipelines}
    for asset in service_map.get("assets", []):
        for pipeline in pipelines:
            if pipeline in asset.get("pipeline_context", []):
                pipeline_asset_types[pipeline].add(asset.get("type", ""))

    # Strategy 1: look for writes_to edges pointing at the shared asset
    writing_pipeline = _find_writing_pipeline(shared_asset_id, pipelines, service_map)
    if writing_pipeline:
        others = [p for p in pipelines if p != writing_pipeline]
        if others:
            return writing_pipeline, _pick_downstream(others, pipeline_asset_types)

    # Strategy 2: asset-type heuristics
    upstream_candidates = [
        p for p, types in pipeline_asset_types.items()
        if types & _UPSTREAM_MARKERS
    ]
    downstream_candidates = [
        p for p, types in pipeline_asset_types.items()
        if types & _DOWNSTREAM_MARKERS
    ]
    if upstream_candidates and downstream_candidates:
        return upstream_candidates[0], downstream_candidates[0]

    # Strategy 3: alphabetical — deterministic even if arbitrary
    sorted_p = sorted(pipelines)
    return sorted_p[0], sorted_p[1]


def _find_writing_pipeline(
    shared_asset_id: str,
    pipelines: list[str],
    service_map: ServiceMap,
) -> str | None:
    """Return the pipeline name whose asset has a 'writes_to' edge to shared_asset_id."""
    for edge in service_map.get("edges", []):
        if edge.get("type") != "writes_to" or edge.get("to_asset") != shared_asset_id:
            continue
        from_asset_id = edge["from_asset"]
        for asset in service_map.get("assets", []):
            if asset["id"] != from_asset_id:
                continue
            for pipeline in pipelines:
                if pipeline in asset.get("pipeline_context", []):
                    return pipeline
    return None


def _pick_downstream(
    candidates: list[str],
    pipeline_asset_types: dict[str, set[str]],
) -> str:
    """Pick the most likely downstream candidate from a list."""
    for p in candidates:
        if pipeline_asset_types.get(p, set()) & _DOWNSTREAM_MARKERS:
            return p
    return candidates[0]
