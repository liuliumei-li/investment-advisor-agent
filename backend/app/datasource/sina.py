"""新浪财经公开数据适配器:指数行情与 7x24 金融快讯(免费无需凭据)。

接口说明(2026-09-21 验证可用):
- 行情:https://hq.sinajs.cn/list=s_sh000001,s_sz399001,...(GB18030 文本,必须带 Referer
  finance.sina.com.cn;字段:名称,现价,涨跌,涨跌%,成交量,成交额)。
  注:东方财富 push2 行情接口对频繁调用限流断连(RemoteProtocolError),故行情改用新浪;
  备用源腾讯 qt.gtimg.cn(q=sh000001,...)格式相近。
- 快讯:https://zhibo.sina.com.cn/api/zhibo/feed(7x24 金融快讯直播流 zhibo_id=152,JSON)。
"""

import logging
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.datasource.base import SOURCE_TYPE_NEWS, SOURCE_TYPE_QUOTE, DataPoint, DataSource

logger = logging.getLogger(__name__)

# 7x24 金融快讯直播流(新浪财经)
FINANCE_FEED_ID = "152"
NEWS_PAGE_SIZE = 10

# 大盘研判覆盖的宽基指数(BR-DAT-01 行情源):名称、代码、新浪行情符号
MARKET_INDEXES = [
    ("上证指数", "000001", "s_sh000001"),
    ("深证成指", "399001", "s_sz399001"),
    ("创业板指", "399006", "s_sz399006"),
    ("沪深300", "000300", "s_sh000300"),
]


def _client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.datasource_timeout_seconds,
        trust_env=False,  # 忽略系统/注册表代理直连(与 LLM 客户端一致策略)
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        transport=transport,
    )


class SinaQuoteSource(DataSource):
    """指数实时行情(新浪 hq.sinajs.cn,GB18030 文本解析)。"""

    name = "新浪财经行情"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport  # 单测注入 httpx.MockTransport

    async def fetch(self) -> list[DataPoint]:
        symbols = ",".join(symbol for _, _, symbol in MARKET_INDEXES)
        async with _client(transport=self.transport) as client:
            try:
                response = await client.get(f"https://hq.sinajs.cn/list={symbols}")
                text = response.content.decode("gb18030", errors="replace")
            except (httpx.HTTPError, UnicodeError) as exc:
                raise self.unavailable(f"{type(exc).__name__}:{exc}") from exc
        fetched_at = datetime.now(timezone.utc).isoformat()
        symbol_to_label = {symbol: (label, code) for label, code, symbol in MARKET_INDEXES}
        points: list[DataPoint] = []
        for line in text.splitlines():
            if "=" not in line or '"' not in line:
                continue
            raw_symbol, _, payload = line.partition("=")
            # 行格式 var hq_str_s_sh000001="..." → 剥前缀取符号 s_sh000001
            symbol = raw_symbol.strip().removeprefix("var hq_str_")
            values = payload.strip().strip('";').split(",")
            if len(values) < 4 or not values[0]:
                continue
            label, code = symbol_to_label.get(symbol, (symbol, ""))
            points.append(
                DataPoint(
                    source_name=self.name,
                    source_type=SOURCE_TYPE_QUOTE,
                    data_point=(
                        f"{label}({code}):{values[0]} {values[1]},涨跌 {values[2]},{values[3]}%"
                    ),
                    source_url=f"https://finance.sina.com.cn/realstock/company/{symbol.split('_')[1]}/nc.shtml",
                    data_timestamp=fetched_at,
                )
            )
        if not points:
            raise self.unavailable("指数行情全部拉取失败")
        return points


class SinaNewsSource(DataSource):
    """新浪 7x24 金融快讯(大盘研判的资讯上下文)。"""

    name = "新浪财经快讯"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport  # 单测注入 httpx.MockTransport

    async def fetch(self) -> list[DataPoint]:
        async with _client(transport=self.transport) as client:
            try:
                response = await client.get(
                    "https://zhibo.sina.com.cn/api/zhibo/feed",
                    params={
                        "page": "1",
                        "page_size": str(NEWS_PAGE_SIZE),
                        "zhibo_id": FINANCE_FEED_ID,
                        "tag_id": "0",
                    },
                )
                result = response.json().get("result") or {}
            except (httpx.HTTPError, ValueError) as exc:
                raise self.unavailable(f"{type(exc).__name__}:{exc}") from exc
        rows = (result.get("data") or {}).get("feed") or {}
        items = rows.get("list") or []
        points = []
        for item in items:
            text = (item.get("rich_text") or "").strip()
            if not text:
                continue
            points.append(
                DataPoint(
                    source_name=self.name,
                    source_type=SOURCE_TYPE_NEWS,
                    data_point=text[:200],
                    source_url=item.get("docurl") or "https://finance.sina.com.cn/7x24/",
                    data_timestamp=_feed_time(item.get("create_time")) or datetime.now(timezone.utc).isoformat(),
                )
            )
        if not points:
            raise self.unavailable("快讯列表为空")
        return points


def _feed_time(create_time: str | None) -> str | None:
    """新浪直播时间戳(如 '2026-09-21 11:23:45')→ ISO 字符串。"""
    if not create_time:
        return None
    return create_time.replace(" ", "T") + "+08:00"
