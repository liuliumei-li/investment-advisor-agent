"""市场数据编排(US-06 子任务 1):多源并发拉取、行情缓存、故障降级标注(BR-PER-04)。

- 行情源带 quote:{code} 5 秒缓存(BR-DAT-03:值内含时间戳,读取时校验时效);
- 任一源失败不阻断整体:降级源列表标注于返回结果(UC-02 扩展流程 3a);
- 全源失败抛 DataSourceUnavailable(50003)。
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.cache.redis_client import QUOTE_CACHE_TTL_SECONDS, Cache, quote_cache_key
from app.core.exceptions import DataSourceUnavailable
from app.datasource.base import DataPoint, DataSource

logger = logging.getLogger(__name__)


class MarketDataService:
    """数据源编排:并发拉取 + 降级标注 + 行情缓存(不碰具体协议,协议在各适配器)。"""

    def __init__(self, cache: Cache, quote_source: DataSource, news_source: DataSource, research_source: DataSource):
        self.cache = cache
        self.quote_source = quote_source
        self.news_source = news_source
        self.research_source = research_source

    async def fetch_market_snapshot(self) -> dict:
        """并发拉取行情/快讯/研报,返回 {quotes, news, research, degraded, fetched_at}。"""
        cached_quotes = await self._cached_quotes()
        results = await asyncio.gather(
            _safe_fetch(self.news_source),
            _safe_fetch(self.research_source),
            return_exceptions=True,
        )
        news_result, research_result = results
        quotes, degraded = cached_quotes
        news, news_err = news_result
        research, research_err = research_result
        if news_err:
            degraded.append({"source": self.news_source.name, "reason": str(news_err)})
        if research_err:
            degraded.append({"source": self.research_source.name, "reason": str(research_err)})
        if not quotes and not news and not research:
            raise DataSourceUnavailable("行情/资讯/研报数据源全部不可用,请稍后重试")
        return {
            "quotes": quotes,
            "news": news,
            "research": research,
            "degraded": degraded,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _cached_quotes(self) -> tuple[list[DataPoint], list[dict]]:
        """行情缓存策略:命中且未过期(5 秒)直接用缓存;未命中/过期则重新拉取。

        缓存值结构 {"points": [...], "fetched_at": iso},points 由 DataPoint dataclass 序列化。
        缓存读写失败降级为直接拉取(写透失效,TTL 兜底)。
        """
        try:
            cached = await self.cache.get_json(quote_cache_key("market"))
            if cached and _fresh_quotes(cached):
                return _points_from_cache(cached), []
        except Exception:  # noqa: BLE001 缓存层异常不阻断数据主流程
            logger.warning("行情缓存读取异常,降级为直接拉取", exc_info=True)
        try:
            quotes = await self.quote_source.fetch()
            await self.cache.set_json(
                quote_cache_key("market"),
                {"points": [point.__dict__ for point in quotes], "fetched_at": datetime.now(timezone.utc).isoformat()},
                ttl=QUOTE_CACHE_TTL_SECONDS,
            )
            return quotes, []
        except DataSourceUnavailable as exc:
            logger.warning("行情源不可用:%s", exc)
            if cached := await self.cache.get_json(quote_cache_key("market")):
                # BR-PER-04:源故障时使用标注时效的缓存数据
                return _points_from_cache(cached), [{"source": self.quote_source.name, "reason": f"{exc}(缓存兜底)"}]
            return [], [{"source": self.quote_source.name, "reason": str(exc)}]


async def _safe_fetch(source: DataSource) -> tuple[list[DataPoint], Exception | None]:
    try:
        return await source.fetch(), None
    except DataSourceUnavailable as exc:
        return [], exc
    except Exception as exc:  # noqa: BLE001 适配器异常统一降级
        logger.warning("数据源 %s 拉取异常:%s", source.name, exc)
        return [], DataSourceUnavailable(f"数据源「{source.name}」不可用:{exc}")


def _points_from_cache(cached: dict) -> list[DataPoint]:
    """缓存 dict 还原为 DataPoint(缓存值为 JSON 原生类型)。"""
    return [DataPoint(**point) for point in cached.get("points") or [] if isinstance(point, dict)]


def _fresh_quotes(cached: dict) -> bool:
    """缓存行情时效校验(BR-DAT-03):5 秒内视为新鲜。"""
    fetched_at = cached.get("fetched_at")
    if not fetched_at:
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
    except ValueError:
        return False
    return 0 <= age < QUOTE_CACHE_TTL_SECONDS
