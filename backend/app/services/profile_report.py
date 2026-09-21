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
- 雷达评分归一(AC-1 chart-ready,单点定义于此,调整须同步 docs/requirements.md):
  risk_level C1→20/C2→40/C3→60/C4→80/C5→100;return_expectation 区间中点按年化 25% 封顶映射
  0~100;investment_horizon 短期→33/中期→67/长期→100;holding_habit_summary 文本无天然数值,
  按最新持仓快照分散度 (1-前三市值占比)×100 计算(无快照为 null,分数时点经 score_updated_at 暴露)。
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.models.user_profile import RISK_LEVEL_LABELS, RiskLevel

# BR-IMG-03 三来源键 → 中文标签(profile_service 与报告服务共用)
SOURCE_KEY_TO_LABEL = {"questionnaire": "问卷", "dialog": "对话", "holdings": "持仓"}

# 画像四要素 → 报告维度键(与 user_profiles 字段名一致)
PROFILE_ELEMENT_FIELDS = ["risk_level", "return_expectation", "investment_horizon", "holding_habit_summary"]

# 报告维度中文标签(AC-1:风险等级/收益预期/投资期限/持仓习惯四要素)
DIMENSION_LABELS = {
    "risk_level": "风险等级",
    "return_expectation": "收益预期",
    "investment_horizon": "投资期限",
    "holding_habit_summary": "持仓习惯",
}

TRACE_SOURCE_QUESTIONNAIRE = "问卷"
TRACE_SOURCE_DIALOG = "对话"
TRACE_SOURCE_HOLDINGS = "持仓"
TRACE_SOURCE_USER_CORRECTION = "用户修正"

# 风险等级 → 雷达分数(五档等距,BR-IMG-01)
RISK_LEVEL_SCORE_MAP = {
    RiskLevel.C1: 20,
    RiskLevel.C2: 40,
    RiskLevel.C3: 60,
    RiskLevel.C4: 80,
    RiskLevel.C5: 100,
}

# 投资期限 → 雷达分数(三档等距)
HORIZON_SCORE_MAP = {"短期": 33, "中期": 67, "长期": 100}

# 收益预期映射分母:区间中点年化达到 25% 及以上视为满分
EXPECTATION_SCORE_CAP = 25.0

# BR-IMG-04 基线参考矩阵:风险等级 → 股票类资产建议仓位上限(requirements.md §6.1)
STOCK_CAP_BY_RISK: dict[RiskLevel, str] = {
    RiskLevel.C1: "≤20%",
    RiskLevel.C2: "≤40%",
    RiskLevel.C3: "40%~60%",
    RiskLevel.C4: "60%~80%",
    RiskLevel.C5: "不限",
}


def stock_cap_for_level(level: RiskLevel) -> str:
    """风险等级 → 股票类仓位上限(BR-IMG-04,建议个性化匹配的确定性依据)。"""
    return STOCK_CAP_BY_RISK[level]


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


# ---- 雷达评分归一(AC-1,规则登记于 requirements.md v1.3) ----


def expectation_score(low: float | None, high: float | None) -> int | None:
    """收益预期区间 → 雷达分数:中点按年化 25% 封顶映射 0~100;无值返回 None。"""
    if low is None or high is None:
        return None
    midpoint = (float(low) + float(high)) / 2
    return min(100, round(midpoint / EXPECTATION_SCORE_CAP * 100))


def holding_habit_score(top3_share: Decimal | float | None) -> int | None:
    """持仓习惯轴数值:最新快照分散度 (1-前三市值占比)×100;无持仓快照返回 None。"""
    if top3_share is None:
        return None
    return round((1 - float(top3_share)) * 100)


def _percent_text(value: float) -> str:
    return f"{value:g}"


def _dimension_trace(elements: dict, field: str) -> dict:
    """元素溯源(缺省为 null:存量画像迁移前无 source_trace)。"""
    entry = elements.get(field)
    if not isinstance(entry, dict):
        return {"source": None, "quote": None, "source_version": None, "updated_at": None}
    return {
        "source": entry.get("source"),
        "quote": entry.get("quote"),
        "source_version": entry.get("version"),
        "updated_at": entry.get("updated_at"),
    }


def build_dimensions(profile, source_trace: dict | None, holdings_info: dict | None = None) -> list[dict]:
    """构建四要素报告维度(定长数组,AC-1 chart-ready):文本展示值 + 雷达分数 + 逐要素溯源。

    holdings_info: {"top3_share": Decimal, "updated_at": "ISO"}(来自最新持仓快照),无快照为 None。
    """
    trace = ensure_trace_structure(source_trace)
    elements = trace["elements"]

    risk = profile.risk_level
    risk_dimension = {
        "key": "risk_level",
        "label": DIMENSION_LABELS["risk_level"],
        "display": risk.value if risk else None,
        "display_label": RISK_LEVEL_LABELS[risk] if risk else None,
        "score": RISK_LEVEL_SCORE_MAP.get(risk),
        **_dimension_trace(elements, "risk_level"),
    }

    low = float(profile.return_expectation_low) if profile.return_expectation_low is not None else None
    high = float(profile.return_expectation_high) if profile.return_expectation_high is not None else None
    expectation_dimension = {
        "key": "return_expectation",
        "label": DIMENSION_LABELS["return_expectation"],
        "display": f"{_percent_text(low)}%~{_percent_text(high)}%" if low is not None and high is not None else None,
        "display_label": None,
        "score": expectation_score(low, high),
        **_dimension_trace(elements, "return_expectation"),
    }

    horizon = profile.investment_horizon
    horizon_dimension = {
        "key": "investment_horizon",
        "label": DIMENSION_LABELS["investment_horizon"],
        "display": horizon,
        "display_label": None,
        "score": HORIZON_SCORE_MAP.get(horizon),
        **_dimension_trace(elements, "investment_horizon"),
    }

    habit_score = None
    score_updated_at = None
    if holdings_info and holdings_info.get("top3_share") is not None:
        habit_score = holding_habit_score(holdings_info["top3_share"])
        score_updated_at = holdings_info.get("updated_at")
    habit_dimension = {
        "key": "holding_habit_summary",
        "label": DIMENSION_LABELS["holding_habit_summary"],
        "display": profile.holding_habit_summary,
        "display_label": None,
        "score": habit_score,
        **_dimension_trace(elements, "holding_habit_summary"),
    }
    habit_dimension["score_updated_at"] = score_updated_at

    dimensions = [risk_dimension, expectation_dimension, horizon_dimension, habit_dimension]
    for dimension in dimensions[:-1]:
        dimension["score_updated_at"] = None
    return dimensions


def serialize_profile_compact(profile) -> dict:
    """当前画像紧凑视图(与 GET /profile 响应同形):JSON 原生类型,可直接入缓存。

    注意:不得直接缓存 ORM 对象序列化结果(Decimal 会经 default=str 变成字符串)。
    """
    return {
        "risk_level": profile.risk_level.value,
        "risk_level_name": RISK_LEVEL_LABELS[profile.risk_level],
        "return_expectation_low": float(profile.return_expectation_low)
        if profile.return_expectation_low is not None
        else None,
        "return_expectation_high": float(profile.return_expectation_high)
        if profile.return_expectation_high is not None
        else None,
        "investment_horizon": profile.investment_horizon,
        "holding_habit_summary": profile.holding_habit_summary,
        "source_mix": profile.source_mix,
        "confidence": float(profile.confidence) if profile.confidence is not None else None,
        # 列默认值在 INSERT 时生效,构造态对象为 None;持久化行必为 False/True
        "confirmed": bool(profile.confirmed),
        "version": profile.version,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
