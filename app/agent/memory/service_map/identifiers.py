"""Service map identifier helpers."""


def generate_asset_id(asset_type: str, name: str) -> str:
    """Generate stable asset ID from type and name."""
    clean_name = name.replace(":", "_").replace("/", "_")
    return f"{asset_type}:{clean_name}"
