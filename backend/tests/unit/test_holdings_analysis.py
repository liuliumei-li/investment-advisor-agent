"""持仓分析引擎单测(US-03 Task 3):集中度、分布、换手、风险推导(纯函数)。"""

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationFailed
from app.models.holding import AssetType
from app.models.user_profile import RiskLevel
from app.services.holdings_analysis import (
    analyze_holdings,
    analyze_turnover,
    build_holding_summary,
    infer_risk_level,
    risk_deviation,
)


def _row(asset_type=AssetType.STOCK, code="600519", name="贵州茅台", quantity="100", cost_price="1500"):
    return {
        "asset_type": asset_type,
        "code": code,
        "name": name,
        "quantity": Decimal(quantity),
        "cost_price": Decimal(cost_price),
    }


class TestInferRiskLevel:
    def test_boundaries(self):
        assert infer_risk_level(Decimal("0")) is RiskLevel.C1
        assert infer_risk_level(Decimal("0.20")) is RiskLevel.C1
        assert infer_risk_level(Decimal("0.201")) is RiskLevel.C2
        assert infer_risk_level(Decimal("0.40")) is RiskLevel.C2
        assert infer_risk_level(Decimal("0.60")) is RiskLevel.C3
        assert infer_risk_level(Decimal("0.80")) is RiskLevel.C4
        assert infer_risk_level(Decimal("0.81")) is RiskLevel.C5
        assert infer_risk_level(Decimal("1")) is RiskLevel.C5

    def test_deviation_gap_two_or_more_triggers(self):
        assert risk_deviation(RiskLevel.C2, RiskLevel.C4) is True
        assert risk_deviation(RiskLevel.C1, RiskLevel.C3) is True
        assert risk_deviation(RiskLevel.C2, RiskLevel.C3) is False
        assert risk_deviation(RiskLevel.C3, RiskLevel.C3) is False


class TestAnalyzeHoldings:
    def test_distribution_concentration_and_inferred_level(self):
        rows = [
            _row(code="600519", name="贵州茅台", quantity="100", cost_price="1500"),  # 150000
            _row(code="000858", name="五粮液", quantity="200", cost_price="100"),  # 20000
            _row(AssetType.ETF, code="510300", name="沪深300ETF", quantity="1000", cost_price="4"),  # 4000
            _row(AssetType.FUND, code="", name="某货币基金", quantity="500", cost_price="2"),  # 1000
        ]
        result = analyze_holdings(rows)
        assert result["holding_count"] == 4
        assert result["total_market_value"] == Decimal("175000")
        assert result["top_holdings"][0]["name"] == "贵州茅台"
        assert result["concentration"]["level"] == "高"
        # 股票市值占比 (150000+20000)/175000 ≈ 0.9714 → C5
        assert result["inferred_risk_level"] == RiskLevel.C5.value
        dist = result["asset_distribution"]
        assert dist[0]["asset_type"] == AssetType.STOCK.value
        assert dist[0]["label"] == "股票"
        assert dist[0]["count"] == 2
        assert dist[0]["share"] == Decimal("170000") / Decimal("175000")

    def test_balanced_portfolio_not_concentrated(self):
        rows = [
            _row(code="A", name="甲", quantity="1", cost_price="100"),
            _row(code="B", name="乙", quantity="1", cost_price="100"),
            _row(code="C", name="丙", quantity="1", cost_price="100"),
            _row(AssetType.ETF, code="D", name="丁ETF", quantity="1", cost_price="100"),
            _row(AssetType.FUND, code="E", name="戊基金", quantity="1", cost_price="100"),
        ]
        result = analyze_holdings(rows)
        assert result["concentration"]["level"] == "适中"
        # 股票占比 0.6 → C3
        assert result["inferred_risk_level"] == RiskLevel.C3.value

    def test_empty_holdings_rejected(self):
        with pytest.raises(ValidationFailed, match="当前无持仓数据"):
            analyze_holdings([])


class TestAnalyzeTurnover:
    def test_detects_new_closed_and_adjusted(self):
        previous = [
            _row(code="600519", name="贵州茅台", quantity="100"),
            _row(code="000858", name="五粮液", quantity="100"),
            _row(code="601318", name="中国平安", quantity="50"),
        ]
        current = [
            _row(code="600519", name="贵州茅台", quantity="150"),  # 加仓
            _row(code="000858", name="五粮液", quantity="100"),  # 不变
            _row(code="300750", name="宁德时代", quantity="10"),  # 新建
        ]
        result = analyze_turnover(current, previous)
        assert result["has_history"] is True
        assert result["new_positions"] == ["宁德时代"]
        assert result["closed_positions"] == ["中国平安"]
        assert result["increased_positions"] == ["贵州茅台"]
        assert result["decreased_positions"] == []
        assert "新建仓 1 只" in result["summary"]
        assert "清仓 1 只" in result["summary"]
        assert "加仓 1 只" in result["summary"]

    def test_unchanged_structure(self):
        previous = [_row(code="600519", name="贵州茅台")]
        result = analyze_turnover(previous, previous)
        assert "持仓结构与上期一致" in result["summary"]

    def test_no_history(self):
        result = analyze_turnover([_row()], None)
        assert result["has_history"] is False
        assert "暂无历史快照" in result["summary"]


class TestBuildHoldingSummary:
    def test_summary_contains_concentration_distribution_and_turnover(self):
        rows = [
            _row(code="600519", name="贵州茅台", quantity="100", cost_price="1500"),
            _row(AssetType.ETF, code="510300", name="沪深300ETF", quantity="1000", cost_price="4"),
        ]
        analysis = analyze_holdings(rows)
        turnover = analyze_turnover(rows, None)
        summary = build_holding_summary(analysis, turnover)
        assert "贵州茅台" in summary
        assert "集中度高" in summary
        assert "股票" in summary and "ETF" in summary
        assert "持仓 2 只" in summary
        assert turnover["has_history"] is False  # 无历史时摘要不含换手
        assert "换手" not in summary
