"""OpenObserve 集成验证器——仅做配置存在性检查。"""

from __future__ import annotations

from typing import Any

from integrations.verification import register_verifier, result


@register_verifier("openobserve")
def verify_openobserve(source: str, config: dict[str, Any]) -> dict[str, str]:
    """验证 OpenObserve 集成配置是否齐全。

    仅检查配置字段的存在性，不发起任何网络连接探测：base_url 非空，
    且提供了 api_token，或同时提供了 username 与 password，即视为通过。

    入参：
        source：配置来源标识。
        config：集成配置字典，可能包含 base_url、api_token、username、password。
    返回：
        验证结果字典；配置缺失时返回状态为 "missing" 的结果，
        配置齐全时返回状态为 "passed" 的结果。
    """
    base_url = str(config.get("base_url", "")).strip()
    api_token = str(config.get("api_token", "")).strip()
    username = str(config.get("username", "")).strip()
    password = str(config.get("password", "")).strip()
    if not base_url:
        return result("openobserve", source, "missing", "Missing base_url.")
    if not (api_token or (username and password)):
        return result("openobserve", source, "missing", "Missing API token or username/password.")
    return result(
        "openobserve", source, "passed", f"Configured for OpenObserve at {base_url.rstrip('/')}."
    )
