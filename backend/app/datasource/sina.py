"""新浪财经公开数据适配器:7x24 金融快讯(免费无需凭据)。

接口说明(2026-09-21 验证可用):https://zhibo.sina.com.cn/api/zhibo/feed
(7x24 金融快讯直播流 zhibo_id=152,返回 JSON,富文本含标题与链接)。
"""

import logging
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from app.datasource.base import SOURCE_TYPE_NEWS, DataPoint, DataSource

logger = logging.getLogger(__name__)

# 7x24 金融快讯直播流(新浪财经)
FINANCE_FEED_ID = "152"
NEWS_PAGE_SIZE = 10


class SinaNewsSource(DataSource):
    """新浪 7x24 金融快讯(大盘研判的资讯上下文)。"""

    name = "新浪财经快讯"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport  # 单测注入 httpx.MockTransport

    async def fetch(self) -> list[DataPoint]:
        async with httpx.AsyncClient(
            timeout=settings.datasource_timeout_seconds,
            trust_env=False,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
            transport=self.transport,
        ) as client:
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
