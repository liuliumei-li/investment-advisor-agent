"""数据源适配器单测(US-06 子任务 1):接口解析、溯源字段、故障语义(MockTransport 注入,不走真实网络)。"""

import json

import httpx
import pytest

from app.core.exceptions import DataSourceUnavailable
from app.datasource.eastmoney import EastmoneyResearchSource, _parse_jsonp
from app.datasource.sina import SinaNewsSource, SinaQuoteSource, _feed_time
from app.datasource.skillhub import SkillHubSource


def _transport(json_body) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json=json_body))


def _transport_text(text) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, text=text))


def _transport_bytes(data: bytes) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, content=data))


SINA_QUOTE_GB18030 = (
    'var hq_str_s_sh000001="上证指数,3949.9068,38.0354,0.97,5023548,94681912";\n'
    'var hq_str_s_sz399001="深证成指,13730.02,89.30,0.65,3000000,50000000";\n'
    'var hq_str_s_sz399006="创业板指,3399.59,27.10,0.80,2000000,40000000";\n'
    'var hq_str_s_sh000300="沪深300,4539.57,32.20,0.71,2500000,45000000";\n'
).encode("gb18030")


class TestSinaQuoteSource:
    async def test_fetch_parses_index_quotes(self):
        source = SinaQuoteSource(transport=_transport_bytes(SINA_QUOTE_GB18030))
        points = await source.fetch()
        assert len(points) == 4  # 上证/深成/创业板/沪深300
        point = points[0]
        assert point.source_name == "新浪财经行情"
        assert point.source_type == "quote"
        assert "上证指数(000001)" in point.data_point
        assert "3949.9068" in point.data_point
        assert "0.97%" in point.data_point
        assert point.source_url.endswith("/sh000001/nc.shtml")
        assert point.data_timestamp  # 抓取时间 ISO

    async def test_empty_response_raises_unavailable(self):
        transport = _transport_bytes(b"")
        source = SinaQuoteSource(transport=transport)
        with pytest.raises(DataSourceUnavailable, match="新浪财经行情"):
            await source.fetch()

    async def test_http_error_raises_unavailable(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(500))
        source = SinaQuoteSource(transport=transport)
        with pytest.raises(DataSourceUnavailable, match="新浪财经行情"):
            await source.fetch()


class TestEastmoneyResearchSource:
    async def test_fetch_parses_jsonp_reports(self):
        body = {
            "hits": 1,
            "data": [
                {
                    "title": "农业银行2026年中报点评",
                    "orgSName": "某券商",
                    "publishDate": "2026-09-21",
                    "emRatingName": "增持",
                    "stockName": "农业银行",
                    "stockCode": "601288",
                    "infoCode": "AP202609211234",
                }
            ],
        }
        source = EastmoneyResearchSource(transport=_transport_text(f"datatable({json.dumps(body)})"))
        points = await source.fetch()
        assert len(points) == 1
        point = points[0]
        assert point.source_name == "东方财富研报"
        assert point.source_type == "research"
        assert "农业银行2026年中报点评" in point.data_point
        assert "增持" in point.data_point
        assert "info/AP202609211234.html" in point.source_url
        assert point.data_timestamp.startswith("2026-09-21")

    async def test_non_jsonp_response_raises(self):
        source = EastmoneyResearchSource(transport=_transport_text("<html>error</html>"))
        with pytest.raises(DataSourceUnavailable, match="东方财富研报"):
            await source.fetch()

    def test_parse_jsonp(self):
        assert _parse_jsonp("datatable({\"a\": 1})") == {"a": 1}
        with pytest.raises(ValueError):
            _parse_jsonp("not jsonp")


class TestSinaNewsSource:
    async def test_fetch_parses_feed(self):
        body = {
            "result": {
                "data": {
                    "feed": {
                        "list": [
                            {
                                "rich_text": "央行开展逆回购操作",
                                "docurl": "https://x",
                                "create_time": "2026-09-21 11:23:45",
                            }
                        ]
                    }
                }
            }
        }
        source = SinaNewsSource(transport=_transport(body))
        points = await source.fetch()
        assert len(points) == 1
        point = points[0]
        assert point.source_name == "新浪财经快讯"
        assert point.source_type == "news"
        assert point.data_point == "央行开展逆回购操作"
        assert point.data_timestamp == "2026-09-21T11:23:45+08:00"

    async def test_empty_feed_raises(self):
        source = SinaNewsSource(transport=_transport({"result": {"data": {"feed": {"list": []}}}}))
        with pytest.raises(DataSourceUnavailable, match="新浪财经快讯"):
            await source.fetch()

    def test_feed_time(self):
        assert _feed_time("2026-09-21 11:23:45") == "2026-09-21T11:23:45+08:00"
        assert _feed_time(None) is None


class TestSkillHubSource:
    async def test_unconfigured_raises_unavailable(self, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "skillhub_token", "")
        source = SkillHubSource()
        with pytest.raises(DataSourceUnavailable, match="SkillHub"):
            await source.fetch()

    async def test_placeholder_raises_even_with_token(self, monkeypatch):
        from app.core import config

        monkeypatch.setattr(config.settings, "skillhub_token", "fake-token")
        source = SkillHubSource()
        with pytest.raises(DataSourceUnavailable, match="尚未实现"):
            await source.fetch()
