"""腾讯个股行情适配器(免费无需凭据,US-08 个股分析)。

接口:https://qt.gtimg.cn/q=sh600519(GBK 文本,~ 分隔,字段索引见 fetch;含价格/涨跌/
换手率/动态PE/PB/振幅/流通与总市值(亿)/52周高低)。东财 push2 限流不可用时的稳定备用。
"""

from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.datasource.base import SOURCE_TYPE_QUOTE, DataPoint, DataSource

# 字段索引(2026-09-22 核对):1 名称 2 代码 3 现价 4 昨收 5 今开 6 成交量(手)
# 31 涨跌额 32 涨跌% 33 最高 34 最低 38 换手率% 39 动态PE 43 振幅%
# 44 流通市值(亿) 45 总市值(亿) 46 PB 47 52周最高 48 52周最低
_IDX = {"name": 1, "price": 3, "prev": 4, "open": 5, "volume": 6, "high": 33, "low": 34,
        "turnover": 38, "pe": 39, "amplitude": 43, "float_cap": 44, "total_cap": 45,
        "pb": 46, "high52": 47, "low52": 48}


class TencentStockQuoteSource(DataSource):
    """单只个股行情(构造时指定股票代码,如 sh600519)。"""

    name = "腾讯个股行情"

    def __init__(self, code: str, transport: httpx.AsyncBaseTransport | None = None):
        self.code = code  # 形如 sh600519 / sz000858
        self.transport = transport

    async def fetch(self) -> list[DataPoint]:
        async with httpx.AsyncClient(
            timeout=settings.datasource_timeout_seconds, trust_env=False,
            headers={"User-Agent": "Mozilla/5.0"}, transport=self.transport,
        ) as client:
            try:
                response = await client.get(f"https://qt.gtimg.cn/q={self.code}")
                text = response.content.decode("gbk", errors="replace")
                payload = text.split('="')[1].rstrip('";\n')
            except (httpx.HTTPError, IndexError, UnicodeError) as exc:
                raise self.unavailable(f"{type(exc).__name__}:{exc}") from exc
        fields = payload.split("~")
        if len(fields) < 49 or not fields[1]:
            raise self.unavailable(f"行情字段不完整:{text[:60]}")

        def value(key: str) -> str:
            return fields[_IDX[key]]

        data_point = (
            f"{fields[1]}({fields[2]}):现价 {value('price')},涨跌 {fields[31]},"
            f"涨跌幅 {fields[32]}%,今开 {value('open')},昨收 {value('prev')},"
            f"最高 {value('high')},最低 {value('low')},换手率 {value('turnover')}%,"
            f"动态PE {value('pe')},PB {value('pb')},振幅 {value('amplitude')}%,"
            f"流通市值 {value('float_cap')}亿,总市值 {value('total_cap')}亿,"
            f"52周区间 {value('low52')}~{value('high52')}"
        )
        return [
            DataPoint(
                source_name=self.name,
                source_type=SOURCE_TYPE_QUOTE,
                data_point=data_point,
                source_url=f"https://gu.qq.com/{self.code}/gp",
                data_timestamp=datetime.now(timezone.utc).isoformat(),
            )
        ]
