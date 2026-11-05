"""Neon API payloads captured from a live project (IDs kept, secrets absent).

Shapes match ``GET /projects/{id}``, ``/branches``, ``/endpoints``,
``/operations``, ``/branches/{id}/storage``, ``/buckets`` and
``/buckets/{name}/objects`` as returned on 2026-11-05. A failed operation and a
second, non-default branch were added to cover the incident-relevant paths.
"""

from __future__ import annotations

from typing import Any

PROJECT_ID = "little-lab-10567887"
MAIN_BRANCH = "br-summer-art-bdbz1xg2"
PREVIEW_BRANCH = "br-quiet-moon-a1b2c3d4"
ENDPOINT_ID = "ep-sparkling-lake-bd31vdlv"

PROJECT: dict[str, Any] = {
    "project": {
        "id": PROJECT_ID,
        "name": "opensre-neon-test",
        "region_id": "aws-us-east-2",
        "pg_version": 18,
        "created_at": "2026-11-05T10:26:04Z",
        "compute_last_active_at": "2026-11-05T10:26:07Z",
        "history_retention_seconds": 86400,
        "settings": {
            "allowed_ips": {"ips": [], "protected_branches_only": False},
            "enable_logical_replication": False,
            "maintenance_window": {"weekdays": [1], "start_time": "05:00", "end_time": "06:00"},
            "block_public_connections": False,
            "block_vpc_connections": False,
            "hipaa": False,
        },
    }
}

BRANCHES: dict[str, Any] = {
    "branches": [
        {
            "id": MAIN_BRANCH,
            "project_id": PROJECT_ID,
            "name": "main",
            "current_state": "ready",
            "state_changed_at": "2026-11-05T10:26:06Z",
            "logical_size": 31686656,
            "primary": True,
            "default": True,
            "protected": False,
            "created_at": "2026-11-05T10:26:04Z",
            "updated_at": "2026-11-05T10:31:15Z",
        },
        {
            "id": PREVIEW_BRANCH,
            "project_id": PROJECT_ID,
            "parent_id": MAIN_BRANCH,
            "name": "preview/checkout-fix",
            "current_state": "init",
            "pending_state": "ready",
            "default": False,
            "protected": False,
            "created_at": "2026-11-05T11:00:00Z",
            "updated_at": "2026-11-05T11:00:01Z",
        },
    ]
}

ENDPOINTS: dict[str, Any] = {
    "endpoints": [
        {
            "host": f"{ENDPOINT_ID}.c-8.us-east-2.aws.neon.tech",
            "id": ENDPOINT_ID,
            "project_id": PROJECT_ID,
            "branch_id": MAIN_BRANCH,
            "autoscaling_limit_min_cu": 1,
            "autoscaling_limit_max_cu": 1,
            "region_id": "aws-us-east-2",
            "type": "read_write",
            "current_state": "idle",
            "pooler_enabled": False,
            "pooler_mode": "transaction",
            "disabled": False,
            "passwordless_access": True,
            "last_active": "2026-11-05T10:26:07Z",
            "suspended_at": "2026-11-05T10:31:15Z",
            "suspend_timeout_seconds": 0,
            "group": {"size": {"min": 1, "max": 1}, "computes": []},
        },
        {
            "host": "ep-young-sun-q9w8e7r6.c-8.us-east-2.aws.neon.tech",
            "id": "ep-young-sun-q9w8e7r6",
            "project_id": PROJECT_ID,
            "branch_id": PREVIEW_BRANCH,
            "autoscaling_limit_min_cu": 0.25,
            "autoscaling_limit_max_cu": 2,
            "type": "read_write",
            "current_state": "init",
            "pending_state": "active",
            "disabled": True,
            "suspend_timeout_seconds": 300,
        },
    ]
}

OPERATIONS: dict[str, Any] = {
    "operations": [
        {
            "id": "9f0e1d2c-0000-4000-8000-000000000001",
            "project_id": PROJECT_ID,
            "branch_id": PREVIEW_BRANCH,
            "endpoint_id": "ep-young-sun-q9w8e7r6",
            "action": "start_compute",
            "status": "failed",
            "error": "compute quota exceeded",
            "failures_count": 3,
            "created_at": "2026-11-05T11:00:02Z",
            "updated_at": "2026-11-05T11:00:40Z",
            "total_duration_ms": 38000,
        },
        {
            "id": "9f0e1d2c-0000-4000-8000-000000000002",
            "project_id": PROJECT_ID,
            "branch_id": PREVIEW_BRANCH,
            "action": "create_timeline",
            "status": "running",
            "failures_count": 0,
            "created_at": "2026-11-05T11:00:00Z",
            "updated_at": "2026-11-05T11:00:01Z",
        },
        {
            "id": "5c878edb-d79a-46f2-b906-b4ea3285cb6a",
            "project_id": PROJECT_ID,
            "branch_id": MAIN_BRANCH,
            "endpoint_id": ENDPOINT_ID,
            "action": "suspend_compute",
            "status": "finished",
            "failures_count": 0,
            "created_at": "2026-11-05T10:31:15Z",
            "updated_at": "2026-11-05T10:31:15Z",
            "total_duration_ms": 826,
        },
    ],
    "pagination": {"cursor": "2026-11-05T10:26:04.069482Z"},
}

STORAGE: dict[str, Any] = {
    "enabled": True,
    "s3_endpoint": f"https://{MAIN_BRANCH}.storage.c-8.us-east-2.aws.neon.tech",
    "region": "us-east-2",
    "force_path_style": True,
}

BUCKETS: dict[str, Any] = {
    "buckets": [
        {
            "name": "incident-artifacts",
            "access_level": "private",
            "created_at": "2026-11-05T10:26:26Z",
        },
        {
            "name": "public-assets",
            "access_level": "public_read",
            "created_at": "2026-11-05T10:26:27Z",
        },
    ]
}

OBJECTS_FOLDED: dict[str, Any] = {
    "folders": ["logs/"],
    "objects": [
        {
            "key": "root.txt",
            "size": 7,
            "last_modified": "2026-11-05T10:26:47Z",
            "etag": "5c2ce561e1e263695dbd267271b86fb8",
        }
    ],
    "prefix": "",
    "is_truncated": False,
}

OBJECTS_PAGE: dict[str, Any] = {
    "folders": [],
    "objects": [
        {
            "key": "logs/2026-11-05/app.json",
            "size": 20,
            "last_modified": "2026-11-05T10:26:30Z",
            "etag": "ba15e5bbca0e53abdfd9a730301d90b1",
        },
        {
            "key": "logs/2026-11-05/worker-1.txt",
            "size": 7,
            "last_modified": "2026-11-05T10:26:39Z",
            "etag": "5c2ce561e1e263695dbd267271b86fb8",
        },
    ],
    "prefix": "logs/",
    "next_cursor": "logs/2026-11-05/worker-1.txt",
    "is_truncated": True,
}

ROUTES: dict[str, dict[str, Any]] = {
    f"/api/v2/projects/{PROJECT_ID}": PROJECT,
    f"/api/v2/projects/{PROJECT_ID}/branches": BRANCHES,
    f"/api/v2/projects/{PROJECT_ID}/endpoints": ENDPOINTS,
    f"/api/v2/projects/{PROJECT_ID}/operations": OPERATIONS,
    f"/api/v2/projects/{PROJECT_ID}/branches/{MAIN_BRANCH}/storage": STORAGE,
    f"/api/v2/projects/{PROJECT_ID}/branches/{MAIN_BRANCH}/buckets": BUCKETS,
    f"/api/v2/projects/{PROJECT_ID}/branches/{MAIN_BRANCH}/buckets/incident-artifacts/objects": (
        OBJECTS_FOLDED
    ),
}
