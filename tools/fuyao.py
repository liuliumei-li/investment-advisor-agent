"""同花顺金融数据服务（扶摇）工具封装。

文档：https://fuyao.aicubes.cn/docs/api-reference/overview/
鉴权：请求头 X-api-key；HTTP 状态码恒为 200，业务结果看响应信封里的 code（0 = 成功）。
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

BASE_URL = "https://fuyao.aicubes.cn"
API_KEY = os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
USE_MOCK = not API_KEY

CST = timezone(timedelta(hours=8))


def _d(ms: int) -> str:
    """毫秒时间戳转 Asia/Shanghai 日期字符串。"""
    return datetime.fromtimestamp(ms / 1000, CST).strftime("%Y-%m-%d")


def _get(path: str, params: dict) -> dict:
    """统一请求出口：加鉴权头、超时、把业务错误转成异常。"""
    # resp是发送请求后接受结果的变量，先httpx.get去取结果，再resp接受结果，程序会在这里阻塞
    resp = httpx.get(
        f"{BASE_URL}{path}",
        params=params,
        headers={"X-api-key": API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != 0:
        raise RuntimeError(
            f"扶摇接口报错 code={payload.get('code')} message={payload.get('message')}"
        )
    return payload.get("data") or {}

# 接下来再看
@tool
def resolve_thscode(keyword: str, limit: int = 5) -> str:
    """把股票名称或代码解析成同花顺标准代码 thscode。

    查询行情、财务等接口之前必须先调用本工具，因为那些接口只认标准代码
    （如 600519.SH），不认"贵州茅台"。

    Args:
        keyword: 股票名称、简称或代码片段，例如"贵州茅台"、"600519"、"平安"
        limit: 最多返回几条候选，默认 5
    """
    if USE_MOCK:
        return json.dumps(
            [{"thscode": "600519.SH", "name": "贵州茅台", "asset_type": "a-share"}],
            ensure_ascii=False,
        )

    data = _get("/api/meta/tickers/search", {"q": keyword, "limit": limit})
    items = [
        {
            "thscode": it.get("thscode"),
            "name": it.get("name"),
            "asset_type": it.get("asset_type"),
        }
        for it in data.get("item", [])
    ]
    return json.dumps(items, ensure_ascii=False)


@tool
def get_price_snapshot(thscodes: str) -> str:
    """查询 A 股实时行情快照：最新价、涨跌额、涨跌幅、开盘、最高、最低、成交量、成交额。

    Args:
        thscodes: 英文逗号分隔的标准代码，最多 10 个，例如 "600519.SH,000001.SZ"
    """
    if USE_MOCK:
        return json.dumps(
            [{"thscode": "600519.SH", "ticker": "600519", "last_price": 1580.5,
              "price_change": 19.2, "price_change_ratio_pct": 1.23,
              "open_price": 1565.0, "high_price": 1585.0, "low_price": 1560.0,
              "prev_price": 1561.3, "volume": 3098875, "turnover": 3937375200}],
            ensure_ascii=False,
        )

    data = _get("/api/a-share/prices/snapshot", {"thscodes": thscodes})
    return json.dumps(data.get("item", []), ensure_ascii=False)


@tool
def get_price_history(thscode: str, days: int = 60) -> str:
    """查询单只 A 股最近若干自然日的日 K 线（前复权），用于判断趋势和区间涨跌。

    返回压缩后的摘要（区间涨跌幅、最高/最低、最后 5 根 K 线），不是完整序列。

    Args:
        thscode: 标准代码，例如 "600519.SH"
        days: 回溯的自然日数，默认 60，范围 5 到 3650
    """
    if USE_MOCK:
        return json.dumps(
            {"thscode": thscode, "count": 42, "start_date": "2026-10-01",
             "end_date": "2026-11-20", "window_change_pct": 5.2,
             "highest": 1620.0, "lowest": 1480.0, "last_close": 1580.5, "recent": []},
            ensure_ascii=False,
        )

    days = max(5, min(days, 3650))
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000

    data = _get(
        "/api/a-share/prices/historical",
        {
            "thscode": thscode,
            "interval": "1d",
            "start": start_ms,
            "end": end_ms,
            "adjust": "forward",
        },
    )
    bars = data.get("item", [])
    if not bars:
        return json.dumps(
            {"thscode": thscode, "count": 0, "message": "该区间无行情记录（停牌或代码有误）"},
            ensure_ascii=False,
        )

    closes = [b["close_price"] for b in bars]
    first, last = closes[0], closes[-1]
    summary = {
        "thscode": thscode,
        "count": len(bars),
        "start_date": _d(bars[0]["date_ms"]),
        "end_date": _d(bars[-1]["date_ms"]),
        "window_change_pct": round((last - first) / first * 100, 2) if first else None,
        "highest": max(b["high_price"] for b in bars),
        "lowest": min(b["low_price"] for b in bars),
        "last_close": last,
        "recent": [
            {"date": _d(b["date_ms"]), "close": b["close_price"], "volume": b["volume"]}
            for b in bars[-5:]
        ],
    }
    return json.dumps(summary, ensure_ascii=False)