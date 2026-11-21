"""OpenObserve 集成分类器。"""

from __future__ import annotations

import logging
from typing import Any

from integrations._validation_helpers import report_classify_failure
from integrations.config_models import OpenObserveIntegrationConfig

logger = logging.getLogger(__name__)


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[OpenObserveIntegrationConfig | None, str | None]:
    """根据凭据判断集成是否可用，并返回集成标识。

    入参：
        credentials：包含 base_url、org、api_token、username、password、
            stream、trace_stream、max_results 等键的凭据字典。
        record_id：集成记录的标识，用于关联配置与错误报告。
    返回：
        二元组 (cfg, "openobserve")：当 base_url 存在且提供了 api_token，
        或提供了 username 与 password 时返回，cfg 为解析后的配置对象；
        否则返回 (None, None)。
    """
    try:
        cfg = OpenObserveIntegrationConfig.model_validate(
            {
                "base_url": credentials.get("base_url", ""),
                "org": credentials.get("org", "default"),
                "api_token": credentials.get("api_token", ""),
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
                "stream": credentials.get("stream", ""),
                "trace_stream": credentials.get("trace_stream", ""),
                "max_results": credentials.get("max_results", 100),
                "integration_id": record_id,
            }
        )
    except Exception as exc:
        report_classify_failure(exc, logger=logger, integration="openobserve", record_id=record_id)
        return None, None
    if cfg.base_url and (cfg.api_token or (cfg.username and cfg.password)):
        return cfg, "openobserve"
    return None, None
