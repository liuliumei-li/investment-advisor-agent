"""外部数据源适配层(architecture.md §2.1):统一 DataSource 抽象 + 白名单校验 + 溯源标识。

默认组合(免费公开源,2026-09-21 验证可用;SkillHub 占位待凭据):
- 行情:新浪指数快照(东财 push2 限流频繁,已弃用);快讯:新浪 7x24;研报:东方财富研报列表。
"""

from fastapi import Depends

from app.cache.redis_client import Cache, get_cache
from app.datasource.base import DataPoint, DataSource
from app.datasource.eastmoney import EastmoneyBoardSource, EastmoneyResearchSource
from app.datasource.market_data import MarketDataService
from app.datasource.sina import SinaNewsSource, SinaQuoteSource
from app.datasource.skillhub import SkillHubSource


def get_market_data_service(cache: Cache = Depends(get_cache)) -> MarketDataService:
    """FastAPI 依赖:默认三源组合(测试经 dependency_overrides 替换为假源)。"""
    return MarketDataService(
        cache,
        quote_source=SinaQuoteSource(),
        news_source=SinaNewsSource(),
        research_source=EastmoneyResearchSource(),
        board_source=EastmoneyBoardSource(),
    )


__all__ = [
    "DataPoint",
    "DataSource",
    "EastmoneyBoardSource",
    "EastmoneyResearchSource",
    "MarketDataService",
    "SinaNewsSource",
    "SinaQuoteSource",
    "SkillHubSource",
    "get_market_data_service",
]
