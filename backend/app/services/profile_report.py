"""画像报告纯函数(US-04):逐要素溯源(trace)读写与报告维度构建。

纯函数模块,便于单测;无 I/O、不反向依赖 Service。画像要素溯源持久化于
user_profiles.source_trace(JSON),结构单点定义于此,调整须同步 docs/requirements.md:

{
  "elements": {
    "risk_level": {"source": "问卷|对话|持仓|用户修正", "quote": null|"原文证据", "version": 3, "updated_at": "ISO"},
    ...
  },
  "conflicts": [
    {"field": "risk_level", "current": "C4", "proposed": "C3", "source": "对话",
     "quote": "...", "version": 2, "created_at": "ISO"}
  ]
}

- elements 只存来源元信息,不存元素值(值以画像行为单一事实源);
- conflicts 按 field upsert(同一要素只保留最新一条待确认冲突);
- 字段值被问卷重提/用户修正重写时,删除该字段的 conflict(避免过期"幽灵冲突");
- trace 值必须为 JSON 原生类型(Decimal/datetime 先转换,SQLite 下 JSON 列无 in-place 突变追踪,必须整体赋新 dict)。
"""

from datetime import datetime, timezone

# BR-IMG-03 三来源键 → 中文标签(profile_service 与报告服务共用)
SOURCE_KEY_TO_LABEL = {"questionnaire": "问卷", "dialog": "对话", "holdings": "持仓"}

# 画像四要素 → 报告维度键(与 user_profiles 字段名一致)
PROFILE_ELEMENT_FIELDS = ["risk_level", "return_expectation", "investment_horizon", "holding_habit_summary"]

TRACE_SOURCE_QUESTIONNAIRE = "问卷"
TRACE_SOURCE_DIALOG = "对话"
TRACE_SOURCE_HOLDINGS = "持仓"
TRACE_SOURCE_USER_CORRECTION = "用户修正"


def incomplete_sources(source_mix: dict | None) -> list[str]:
    """尚未收集的画像来源(BR-IMG-03 提示,UC-01)。"""
    return [label for key, label in SOURCE_KEY_TO_LABEL.items() if key not in (source_mix or {})]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_trace_structure(trace: dict | None) -> dict:
    """NULL/旧结构归一为 {elements: {}, conflicts: []}(存量画像 source_trace 为 NULL 时安全)。"""
    if not isinstance(trace, dict):
        return {"elements": {}, "conflicts": []}
    elements = trace.get("elements") if isinstance(trace.get("elements"), dict) else {}
    conflicts = trace.get("conflicts") if isinstance(trace.get("conflicts"), list) else []
    return {"elements": dict(elements), "conflicts": list(conflicts)}


def build_trace_entry(source: str, quote: str | None, version: int) -> dict:
    """元素溯源条目:来源 + 原文引用(问卷/持仓无原文为 null)+ 版本 + 时间戳(BR-DAT-04)。"""
    return {"source": source, "quote": quote, "version": version, "updated_at": _utc_now_iso()}


def set_element_trace(trace: dict | None, field: str, entry: dict) -> dict:
    """写入/覆盖元素溯源(整体返回新 dict,JSON 列突变追踪要求)。"""
    normalized = ensure_trace_structure(trace)
    normalized["elements"][field] = entry
    return normalized


def remove_element_trace(trace: dict | None, field: str) -> dict:
    """删除元素溯源(元素值被清空时保持溯源与值一致)。"""
    normalized = ensure_trace_structure(trace)
    normalized["elements"].pop(field, None)
    return normalized


def upsert_conflict(
    trace: dict | None,
    field: str,
    current,
    proposed,
    source: str,
    quote: str | None,
    version: int,
) -> dict:
    """记录待确认冲突(BR-IMG-05:冲突保留原值并披露,待用户在画像报告确认/修正)。

    同一字段只保留最新一条:后续新的冲突主张替换旧条目。
    """
    normalized = ensure_trace_structure(trace)
    entry = {
        "field": field,
        "current": current,
        "proposed": proposed,
        "source": source,
        "quote": quote,
        "version": version,
        "created_at": _utc_now_iso(),
    }
    conflicts = [c for c in normalized["conflicts"] if c.get("field") != field]
    conflicts.append(entry)
    normalized["conflicts"] = conflicts
    return normalized


def remove_conflict(trace: dict | None, field: str) -> dict:
    """字段值被重写(问卷重提/用户修正)时删除该字段的待确认冲突。"""
    normalized = ensure_trace_structure(trace)
    normalized["conflicts"] = [c for c in normalized["conflicts"] if c.get("field") != field]
    return normalized


def clear_conflicts(trace: dict | None) -> dict:
    """用户确认画像时清空全部待确认冲突(BR-IMG-05 确认语义)。"""
    normalized = ensure_trace_structure(trace)
    normalized["conflicts"] = []
    return normalized
