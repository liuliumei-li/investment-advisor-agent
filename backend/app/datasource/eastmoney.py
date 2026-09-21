"""东方财富公开数据适配器(免费无需凭据):指数行情与券商研报。

接口说明(2026-09-21 验证可用,直连 trust_env=False 避免本机注册表代理干扰):
- 行情:https://push2.eastmoney.com/api/qt/stock/get(secid 区分市场:1=沪、0=深/创业板)
- 研报:https://reportapi.eastmoney.com/report/list(JSONP 包装 datatable(...),须剥壳)
"""

import json
import logging
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.datasource.base import SOURCE_TYPE_QUOTE, SOURCE_TYPE_RESEARCH, DataPoint, DataSource

logger = logging.getLogger(__name__)

# 大盘研判覆盖的宽基指数(BR-DAT-01 行情源):名称、市场代码、secid
MARKET_INDEXES = [
    ("上证指数", "000001", "1.000001"),
    ("深证成指", "399001", "0.399001"),
    ("创业板指", "399006", "0.399006"),
    ("沪深300", "000300", "1.000300"),
]

# 研报列表每页条数(研判上下文取最新一页)
RESEARCH_PAGE_SIZE = 5


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.datasource_timeout_seconds,
        trust_env=False,  # 忽略系统/注册表代理直连(与 LLM 客户端一致策略)
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
        transport=transport,
    )


class EastmoneyQuoteSource(DataSource):
    """指数实时行情(secid 秒级快照,缓存策略在 market_data 编排层)。"""

    name = "东方财富行情"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport  # 单测注入 httpx.MockTransport

    async def fetch(self) -> list[DataPoint]:
        points: list[DataPoint] = []
        fetched_at = datetime.now(timezone.utc).isoformat()
        async with _client(transport=self.transport) as client:
            for label, code, secid in MARKET_INDEXES:
                try:
                    response = await client.get(
                        "https://push2.eastmoney.com/api/qt/stock/get",
                        params={"secid": secid, "fields": "f43,f57,f58,f60,f169,f170,f46"},
                    )
                    data = response.json().get("data") or {}
                    price = data.get("f43")
                    if price is None or data.get("f58") is None:
                        raise ValueError(f"接口返回缺少行情字段:{response.text[:80]}")
                    # f43 为千分位整数(如 394991 = 3949.91),按 f57 小数位解码
                    decimals = data.get("f59", 2) or 2
                    points.append(
                        DataPoint(
                            source_name=self.name,
                            source_type=SOURCE_TYPE_QUOTE,
                            data_point=(
                                f"{label}({code}):{data['f58']} {float(price) / 10**decimals:.{decimals}f},"
                                f"涨跌 {float(data.get('f169') or 0) / 100:.2f}%,"
                                f"今开 {float(data.get('f46') or 0) / 10**decimals:.{decimals}f},"
                                f"昨收 {float(data.get('f60') or 0) / 10**decimals:.{decimals}f}"
                            ),
                            source_url=f"https://quote.eastmoney.com/zs{code}.html",
                            data_timestamp=fetched_at,
                        )
                    )
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    logger.warning("行情拉取失败 %s:%s", label, exc)
        if not points:
            raise self.unavailable("指数行情全部拉取失败")
        return points


class EastmoneyResearchSource(DataSource):
    """券商研报列表(JSONP 响应,剥壳取 data 字段)。"""

    name = "东方财富研报"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def fetch(self) -> list[DataPoint]:
        params = {
            "cb": "datatable",
            "industryCode": "*",
            "pageSize": str(RESEARCH_PAGE_SIZE),
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": "2020-01-01",
            "endTime": datetime.now().strftime("%Y-%m-%d"),
            "pageNo": "1",
            "fields": "",
            "qType": "0",
            "orgCode": "",
            "code": "*",
            "rcode": "",
            "p": "1",
            "pageNum": "1",
        }
        async with _client(transport=self.transport) as client:
            try:
                response = await client.get("https://reportapi.eastmoney.com/report/list", params=params)
                data = _parse_jsonp(response.text)
            except (httpx.HTTPError, ValueError) as exc:
                raise self.unavailable(f"{type(exc).__name__}:{exc}") from exc
        reports = data.get("data") or []
        points = []
        for item in reports:
            title = item.get("title") or ""
            info_code = item.get("infoCode") or ""
            points.append(
                DataPoint(
                    source_name=self.name,
                    source_type=SOURCE_TYPE_RESEARCH,
                    data_point=(
                        f"研报《{title}》({item.get('orgSName') or '未知机构'},"
                        f"{item.get('publishDate') or ''}):评级 {item.get('emRatingName') or '未评级'},"
                        f"标的 {item.get('stockName') or ''} {item.get('stockCode') or ''}"
                    ),
                    source_url=f"https://data.eastmoney.com/report/info/{info_code}.html" if info_code else "",
                    data_timestamp=f"{item.get('publishDate') or ''} 00:00:00+08:00",
                )
            )
        if not points:
            raise self.unavailable("研报列表为空")
        return points


def _parse_jsonp(text: str) -> dict:
    """剥 JSONP 包装:datatable({...}) → {...}。"""
    start, end = text.find("("), text.rfind(")")
    if start == -1 or end == -1:
        raise ValueError(f"响应不是 JSONP:{text[:80]}")
    return json.loads(text[start + 1 : end])
