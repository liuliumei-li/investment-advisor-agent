"""东方财富公开数据适配器(免费无需凭据):券商研报。

接口说明(2026-09-21 验证可用,直连 trust_env=False 避免本机注册表代理干扰):
- 研报:https://reportapi.eastmoney.com/report/list(JSONP 包装 datatable(...),须剥壳);
- 行情已改用新浪 hq.sinajs.cn(见 sina.py):push2 行情接口对频繁调用限流断连。
"""

import json
import logging
from datetime import datetime

import httpx

from app.core.config import settings
from app.datasource.base import SOURCE_TYPE_RESEARCH, DataPoint, DataSource

logger = logging.getLogger(__name__)

# 研报列表每页条数(研判上下文取最新一页)
RESEARCH_PAGE_SIZE = 5


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.datasource_timeout_seconds,
        trust_env=False,  # 忽略系统/注册表代理直连(与 LLM 客户端一致策略)
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
        transport=transport,
    )


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
