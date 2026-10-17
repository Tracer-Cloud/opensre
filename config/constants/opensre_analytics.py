"""OpenSRE Analytics integration names shared across layers."""

from __future__ import annotations

OPENSRE_ANALYTICS_URL_ENV = "OPENSRE_ANALYTICS_URL"
OPENSRE_ANALYTICS_API_TOKEN_ENV = "OPENSRE_ANALYTICS_API_TOKEN"
#: Loop report-builder name the scheduled-loop runner maps to the daily growth report.
OPENSRE_ANALYTICS_GROWTH_REPORT_BUILDER = "opensre_analytics_daily_growth"

__all__ = [
    "OPENSRE_ANALYTICS_API_TOKEN_ENV",
    "OPENSRE_ANALYTICS_GROWTH_REPORT_BUILDER",
    "OPENSRE_ANALYTICS_URL_ENV",
]
