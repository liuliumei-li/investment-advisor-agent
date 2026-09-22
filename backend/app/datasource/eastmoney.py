"""东方财富公开数据适配器(免费无需凭据):行业板块行情与券商研报。

接口说明(2026-09-21 验证可用,直连 trust_env=False 避免本机注册表代理干扰):
- 行业板块:https://push2.eastmoney.com/api/qt/clist/get(fs=m:90+t:2 行业板块,含涨跌幅/主力净流入,
  低频调用稳定;指数点位单接口 stock/get 易限流已弃用);
- 研报:https://reportapi.eastmoney.com/report/list(JSONP 包装 datatable(...),须剥壳)。
"""

import json
import logging
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.datasource.base import SOURCE_TYPE_QUOTE, SOURCE_TYPE_RESEARCH, DataPoint, DataSource

logger = logging.getLogger(__name__)

# 行业板块列表条数(研判上下文取涨跌幅前列)
BOARD_PAGE_SIZE = 10

# 研报列表每页条数(研判上下文取最新一页)
RESEARCH_PAGE_SIZE = 5


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.datasource_timeout_seconds,
        trust_env=False,  # 忽略系统/注册表代理直连(与 LLM 客户端一致策略)
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
        transport=transport,
    )


class EastmoneyBoardSource(DataSource):
    """行业板块行情(东财 clist,含涨跌幅/主力净流入,US-07 板块追踪)。"""

    name = "东方财富板块"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport  # 单测注入 httpx.MockTransport

    async def fetch(self) -> list[DataPoint]:
        async with _client(transport=self.transport) as client:
            try:
                response = await client.get(
                    "https://push2.eastmoney.com/api/qt/clist/get",
                    params={
                        "pn": "1",
                        "pz": str(BOARD_PAGE_SIZE),
                        "po": "1",
                        "np": "1",
                        "fltt": "2",
                        "invt": "2",
                        "fid": "f3",
                        "fs": "m:90+t:2+f:!50",  # 行业板块
                        "fields": "f2,f3,f4,f12,f14,f62",
                    },
                )
                data = response.json().get("data") or {}
                rows = data.get("diff") or []
                if not rows:
                    raise ValueError(f"接口返回缺少板块行情:{response.text[:80]}")
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise self.unavailable(f"{type(exc).__name__}:{exc}") from exc
        fetched_at = datetime.now(timezone.utc).isoformat()
        points: list[DataPoint] = []
        for row in rows:
            name = row.get("f14")
            if not name:
                continue
            net_flow = row.get("f62")
            points.append(
                DataPoint(
                    source_name=self.name,
                    source_type=SOURCE_TYPE_QUOTE,
                    data_point=(
                        f"板块 {name}:涨跌幅 {row.get('f3')}%,"
                        f"主力净流入 {net_flow if net_flow is not None else '无数据'}"
                    ),
                    source_url="https://data.eastmoney.com/bkzj/industry.html",
                    data_timestamp=fetched_at,
                )
            )
        if not points:
            raise self.unavailable("板块行情全部拉取失败")
        return points


class EastmoneyFinanceSource(DataSource):
    """个股主要财务指标(F10,US-08 基本面分析;datacenter-web 域不受 push2 限流影响)。"""

    name = "东方财富财务数据"

    def __init__(self, secucode: str, transport: httpx.AsyncBaseTransport | None = None):
        self.secucode = secucode  # 形如 600519.SH
        self.transport = transport

    async def fetch(self) -> list[DataPoint]:
        async with _client(transport=self.transport) as client:
            try:
                response = await client.get(
                    "https://datacenter-web.eastmoney.com/api/data/v1/get",
                    params={
                        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
                        "columns": "ALL",
                        "pageSize": "1",
                        "pageNumber": "1",
                        "filter": f'(SECUCODE="{self.secucode}")',
                    },
                )
                rows = ((response.json() or {}).get("result") or {}).get("data") or []
                if not rows:
                    raise ValueError(f"F10 财务数据为空:{response.text[:80]}")
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                raise self.unavailable(f"{type(exc).__name__}:{exc}") from exc
        row = rows[0]

        def num(key: str) -> str:
            value = row.get(key)
            return f"{value:.2f}" if isinstance(value, (int, float)) else str(value or "无数据")

        data_point = (
            f"{row.get('SECURITY_NAME_ABBR')}({row.get('SECURITY_CODE')}) {row.get('REPORT_DATE_NAME')}财务指标:"
            f"基本EPS {num('EPSJB')}元,每股净资产 {num('BPS')}元,"
            f"营业总收入 {num('TOTALOPERATEREVE')}元(同比 {num('TOTALOPERATEREVETZ')}%),"
            f"归母净利润 {num('PARENTNETPROFIT')}元(同比 {num('PARENTNETPROFITTZ')}%),"
            f"加权ROE {num('ROEJQ')}%,销售毛利率 {num('XSMLL')}%,资产负债率 {num('ZCFZL')}%"
        )
        return [
            DataPoint(
                source_name=self.name,
                source_type=SOURCE_TYPE_RESEARCH,
                data_point=data_point,
                source_url=(
                    "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index"
                    f"?type=web&code={self.secucode.replace('.', '')}"
                ),
                data_timestamp=f"{row.get('REPORT_DATE') or ''}",
            )
        ]


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
