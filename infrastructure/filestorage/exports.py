"""Public names for :mod:`infrastructure.filestorage`, resolved one leaf at a time."""

from __future__ import annotations

from config.package_exports import bind_package_exports

EXPORTS: dict[str, str] = {
    "NO_EXCLUSIONS": "exclusions",
    "NO_EXCLUSIONS_HELP": "messages",
    "BucketExposure": "enums",
    "ExclusionRules": "exclusions",
    "OrgScopeNotSupportedError": "errors",
    "PublicAccessStatus": "exposure",
    "format_exclusion_lines": "messages",
    "format_exposure_line": "messages",
    "format_status_lines": "messages",
    "format_report_lines": "messages",
    "DISABLED_HELP": "messages",
    "BuiltInProvider": "enums",
    "ObjectStore": "contracts",
    "RemoteObject": "contracts",
    "RemoteSyncConfig": "config",
    "RemoteSyncConfigError": "errors",
    "RemoteSyncError": "errors",
    "RemoteSyncUnavailableError": "errors",
    "RemoteSyncSubcommand": "enums",
    "SyncDirection": "enums",
    "SyncReport": "engine",
    "SyncRoot": "syncable",
    "SyncRootName": "enums",
    "SyncRootStatus": "operations",
    "SyncStatus": "operations",
    "UnsyncablePathError": "errors",
    "build_object_store": "providers",
    "check_bucket_exposure": "providers",
    "get_sync_status": "operations",
    "is_syncable": "syncable",
    "load_remote_sync_config": "config",
    "local_files": "engine",
    "parse_exclusions": "exclusions",
    "pull": "engine",
    "push": "engine",
    "relative_key": "engine",
    "remote_sync_enabled": "config",
    "resolve_direction": "engine",
    "resolved_roots": "syncable",
    "root_state": "messages",
    "run_remote_sync": "operations",
    "run_sync": "engine",
    "syncable_roots": "syncable",
}

__all__, __getattr__, __dir__ = bind_package_exports("infrastructure.filestorage", EXPORTS)
