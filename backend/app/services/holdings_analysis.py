"""持仓分析引擎(US-03 AC-2):集中度、资产类别分布、换手特征、实际风险等级推导。

纯函数实现,便于单测;阈值单点定义于此,调整时同步更新 docs/requirements.md(US-03 补充规则):
- 实际持仓风险等级:按 BR-IMG-04 股票类资产市值占比矩阵反推;
- 偏差提示阈值:自评风险等级与持仓实际等级相差 ≥2 档(AC-3);
- 换手特征:最近两个导入快照对比(新建仓/清仓/加仓/减仓)。
"""

from decimal import Decimal

from app.core.exceptions import ValidationFailed
from app.models.holding import ASSET_TYPE_LABELS, AssetType
from app.models.user_profile import RiskLevel

# 集中度结论阈值:单一持仓或前三合计市值占比达到即视为"高度集中"(requirements.md 登记)
CONCENTRATION_SINGLE_HIGH = Decimal("0.40")
CONCENTRATION_TOP3_HIGH = Decimal("0.70")

# BR-IMG-04 反推:股票类资产市值占比 → 实际持仓风险等级(≤ 阈值取对应档,超过全部则为 C5)
STOCK_SHARE_RISK_MAP: list[tuple[Decimal, RiskLevel]] = [
    (Decimal("0.20"), RiskLevel.C1),
    (Decimal("0.40"), RiskLevel.C2),
    (Decimal("0.60"), RiskLevel.C3),
    (Decimal("0.80"), RiskLevel.C4),
]

# AC-3:自评与持仓实际风险等级偏差 ≥2 档时提示用户
RISK_DEVIATION_PROMPT_GAP = 2

_RISK_LEVEL_INDEX: dict[RiskLevel, int] = {
    level: i for i, level in enumerate([RiskLevel.C1, RiskLevel.C2, RiskLevel.C3, RiskLevel.C4, RiskLevel.C5])
}


def _market_value(row: dict) -> Decimal:
    return row["quantity"] * row["cost_price"]


def infer_risk_level(stock_share: Decimal) -> RiskLevel:
    """股票类市值占比 → 实际持仓风险等级(BR-IMG-04 矩阵反推)。"""
    for threshold, level in STOCK_SHARE_RISK_MAP:
        if stock_share <= threshold:
            return level
    return RiskLevel.C5


def risk_deviation(self_assessed: RiskLevel, inferred: RiskLevel) -> bool:
    """自评与持仓实际风险等级偏差是否达到提示阈值(≥2 档,AC-3)。"""
    return abs(_RISK_LEVEL_INDEX[inferred] - _RISK_LEVEL_INDEX[self_assessed]) >= RISK_DEVIATION_PROMPT_GAP


def analyze_holdings(rows: list[dict]) -> dict:
    """集中度 + 资产类别分布 + 实际风险等级(AC-2)。

    rows 为归一后的持仓行 {asset_type: AssetType, code, name, quantity: Decimal, cost_price: Decimal}。
    """
    if not rows:
        raise ValidationFailed("当前无持仓数据,请先导入持仓")
    total = sum((_market_value(r) for r in rows), Decimal("0"))
    items = [
        {
            "code": r["code"],
            "name": r["name"],
            "asset_type": r["asset_type"].value,
            "asset_type_label": ASSET_TYPE_LABELS[r["asset_type"]],
            "market_value": _market_value(r),
            "share": _market_value(r) / total,
        }
        for r in rows
    ]
    items.sort(key=lambda item: item["market_value"], reverse=True)

    by_type: dict[str, dict] = {}
    for r in rows:
        bucket = by_type.setdefault(
            r["asset_type"].value,
            {
                "asset_type": r["asset_type"].value,
                "label": ASSET_TYPE_LABELS[r["asset_type"]],
                "market_value": Decimal("0"),
                "count": 0,
            },
        )
        bucket["market_value"] += _market_value(r)
        bucket["count"] += 1
    distribution = [
        {**bucket, "share": bucket["market_value"] / total}
        for bucket in sorted(by_type.values(), key=lambda b: b["market_value"], reverse=True)
    ]

    top1_share = items[0]["share"]
    top3_share = sum((item["share"] for item in items[:3]), Decimal("0"))
    concentrated = top1_share >= CONCENTRATION_SINGLE_HIGH or top3_share >= CONCENTRATION_TOP3_HIGH
    stock_share = next(
        (b["share"] for b in distribution if b["asset_type"] == AssetType.STOCK.value), Decimal("0")
    )
    return {
        "total_market_value": total,
        "holding_count": len(rows),
        "top_holdings": items[:3],
        "concentration": {
            "top1_share": top1_share,
            "top3_share": top3_share,
            "level": "高" if concentrated else "适中",
        },
        "asset_distribution": distribution,
        "stock_share": stock_share,
        "inferred_risk_level": infer_risk_level(stock_share).value,
    }


def _position_key(row: dict) -> str:
    """持仓行匹配键:优先代码,无代码按名称(跨快照换手对比用)。"""
    return row["code"] or row["name"]


def _display(row: dict) -> str:
    return row["name"] or row["code"]


def analyze_turnover(current: list[dict], previous: list[dict] | None) -> dict:
    """换手特征:最近两个快照对比(AC-2);previous 为 None 表示尚无历史快照。"""
    if previous is None:
        return {"has_history": False, "summary": "暂无历史快照,换手特征待下次导入后对比"}
    prev_map = {_position_key(r): r for r in previous}
    curr_map = {_position_key(r): r for r in current}
    new_positions = [r for k, r in curr_map.items() if k not in prev_map]
    closed_positions = [r for k, r in prev_map.items() if k not in curr_map]
    increased = [r for k, r in curr_map.items() if k in prev_map and r["quantity"] > prev_map[k]["quantity"]]
    decreased = [r for k, r in curr_map.items() if k in prev_map and r["quantity"] < prev_map[k]["quantity"]]

    parts = []
    if new_positions:
        parts.append(f"新建仓 {len(new_positions)} 只")
    if closed_positions:
        parts.append(f"清仓 {len(closed_positions)} 只")
    if increased:
        parts.append(f"加仓 {len(increased)} 只")
    if decreased:
        parts.append(f"减仓 {len(decreased)} 只")
    change = "、".join(parts) if parts else "持仓结构与上期一致"
    return {
        "has_history": True,
        "new_positions": [_display(r) for r in new_positions],
        "closed_positions": [_display(r) for r in closed_positions],
        "increased_positions": [_display(r) for r in increased],
        "decreased_positions": [_display(r) for r in decreased],
        "summary": f"{change}(对比上期 {len(previous)} 只 → 本期 {len(current)} 只)",
    }


def build_holding_summary(analysis: dict, turnover: dict) -> str:
    """持仓习惯摘要(BR-IMG-02 第四要素):集中度、资产结构、换手一句话结论。"""
    conc = analysis["concentration"]
    top_part = "、".join((item["name"] or item["code"]) for item in analysis["top_holdings"][:2])
    dist_part = "、".join(f'{b["label"]}{float(b["share"]) * 100:.1f}%' for b in analysis["asset_distribution"])
    parts = [
        f"持仓 {analysis['holding_count']} 只",
        f"市值集中于 {top_part}" if top_part else "市值分散",
        f"集中度{conc['level']}(前三合计 {float(conc['top3_share']) * 100:.1f}%)",
        f"资产分布:{dist_part}",
    ]
    if turnover["has_history"]:
        parts.append(f"换手:{turnover['summary']}")
    return ";".join(parts) + "。"
