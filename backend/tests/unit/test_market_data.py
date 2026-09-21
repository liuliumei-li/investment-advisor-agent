"""市场数据编排单测(US-06 子任务 1):多源并发、行情缓存时效、故障降级标注(BR-PER-04)。"""

import asyncio

import pytest

from app.cache.redis_client import quote_cache_key
from app.core.exceptions import DataSourceUnavailable
from app.datasource.base import DataPoint, DataSource
from app.datasource.market_data import MarketDataService, _fresh_quotes


class FakeSource(DataSource):
    def __init__(self, name, points=None, error=None, delay=0.0):
        self.name = name
        self.points = points or []
        self.error = error
        self.delay = delay

    async def fetch(self):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.points


def make_point(source_name, data_point="数据", t="2026-09-21T10:00:00+00:00") -> DataPoint:
    return DataPoint(
        source_name=source_name, source_type="quote", data_point=data_point, source_url="", data_timestamp=t
    )


def make_service(cache, quote=None, news=None, research=None):
    return MarketDataService(
        cache,
        quote_source=quote or FakeSource("东方财富行情", []),
        news_source=news or FakeSource("新浪财经快讯", []),
        research_source=research or FakeSource("东方财富研报", []),
    )


class TestFetchMarketSnapshot:
    async def test_three_sources_aggregated(self, cache):
        service = make_service(
            cache,
            quote=FakeSource("东方财富行情", [make_point("东方财富行情", "指数数据")]),
            news=FakeSource("新浪财经快讯", [make_point("新浪财经快讯", "快讯")]),
            research=FakeSource("东方财富研报", [make_point("东方财富研报", "研报")]),
        )
        result = await service.fetch_market_snapshot()
        assert len(result["quotes"]) == 1 and len(result["news"]) == 1 and len(result["research"]) == 1
        assert result["degraded"] == []
        assert result["fetched_at"]

    async def test_source_failure_degraded_not_fatal(self, cache):
        service = make_service(
            cache,
            quote=FakeSource("东方财富行情", [make_point("东方财富行情")]),
            news=FakeSource("新浪财经快讯", error=DataSourceUnavailable("快讯挂了")),
            research=FakeSource("东方财富研报", [make_point("东方财富研报")]),
        )
        result = await service.fetch_market_snapshot()
        assert len(result["news"]) == 0
        degraded = {d["source"] for d in result["degraded"]}
        assert "新浪财经快讯" in degraded

    async def test_all_sources_failed_raises(self, cache):
        service = make_service(
            cache,
            quote=FakeSource("东方财富行情", error=DataSourceUnavailable("挂了")),
            news=FakeSource("新浪财经快讯", error=DataSourceUnavailable("挂了")),
            research=FakeSource("东方财富研报", error=DataSourceUnavailable("挂了")),
        )
        with pytest.raises(DataSourceUnavailable, match="全部不可用"):
            await service.fetch_market_snapshot()

    async def test_quotes_cached_and_reused_within_ttl(self, cache):
        quote = FakeSource("东方财富行情", [make_point("东方财富行情", "行情v1")])
        service = make_service(cache, quote=quote)
        first = await service.fetch_market_snapshot()
        # 第二次:5 秒 TTL 内命中缓存,不重新拉取(计数不变)
        second = await service.fetch_market_snapshot()
        assert first["quotes"] == second["quotes"]
        cached = await cache.get_json(quote_cache_key("market"))
        assert cached is not None and cached["points"][0]["data_point"] == "行情v1"

    async def test_stale_cache_rejected_and_refetched(self, cache):
        from datetime import datetime, timedelta, timezone

        stale = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        stale_value = {"points": [make_point("东方财富行情", "旧行情").__dict__], "fetched_at": stale}
        await cache.set_json(quote_cache_key("market"), stale_value)
        assert _fresh_quotes({"fetched_at": stale}) is False
        service = make_service(cache, quote=FakeSource("东方财富行情", [make_point("东方财富行情", "新行情")]))
        result = await service.fetch_market_snapshot()
        assert result["quotes"][0].data_point == "新行情"

    async def test_quote_source_down_uses_stale_cache_with_flag(self, cache):
        await cache.set_json(
            quote_cache_key("market"),
            {"points": [make_point("东方财富行情", "兜底行情").__dict__], "fetched_at": "2026-09-21T10:00:00+00:00"},
        )
        service = make_service(cache, quote=FakeSource("东方财富行情", error=DataSourceUnavailable("挂了")))
        result = await service.fetch_market_snapshot()
        assert result["quotes"][0].data_point == "兜底行情"
        assert any("缓存兜底" in d["reason"] for d in result["degraded"])


class TestFreshQuotes:
    def test_recent_considered_fresh(self):
        from datetime import datetime, timedelta, timezone

        recent = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        assert _fresh_quotes({"fetched_at": recent}) is True

    def test_missing_or_invalid_timestamp_not_fresh(self):
        assert _fresh_quotes({}) is False
        assert _fresh_quotes({"fetched_at": "not-a-date"}) is False
