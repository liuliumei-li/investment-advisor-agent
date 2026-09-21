"""画像报告纯函数单测(US-04):逐要素溯源读写、待确认冲突生命周期、雷达评分与报告维度。"""

from decimal import Decimal

from app.models.user_profile import RiskLevel, UserProfile
from app.services.profile_report import (
    build_dimensions,
    build_trace_entry,
    clear_conflicts,
    ensure_trace_structure,
    expectation_score,
    holding_habit_score,
    incomplete_sources,
    remove_conflict,
    remove_element_trace,
    serialize_profile_compact,
    set_element_trace,
    upsert_conflict,
)


def make_profile(**overrides) -> UserProfile:
    data = {
        "user_id": 1,
        "risk_level": RiskLevel.C4,
        "return_expectation_low": Decimal("6"),
        "return_expectation_high": Decimal("10"),
        "investment_horizon": "中期",
        "holding_habit_summary": "持仓 3 只;集中度高。",
        "source_mix": {"questionnaire": 0.5, "dialog": 0.5},
        "confidence": Decimal("0.85"),
        "version": 2,
    }
    data.update(overrides)
    return UserProfile(**data)


class TestTraceHelpers:
    def test_build_trace_entry_has_source_quote_version_timestamp(self):
        entry = build_trace_entry("对话", "我能承受 20% 回撤", 3)
        assert entry["source"] == "对话"
        assert entry["quote"] == "我能承受 20% 回撤"
        assert entry["version"] == 3
        assert entry["updated_at"]  # ISO 时间戳非空

    def test_build_trace_entry_null_quote_for_structured_sources(self):
        entry = build_trace_entry("问卷", None, 1)
        assert entry["quote"] is None

    def test_ensure_trace_structure_normalizes_none(self):
        assert ensure_trace_structure(None) == {"elements": {}, "conflicts": []}

    def test_ensure_trace_structure_keeps_existing(self):
        trace = {"elements": {"risk_level": {"source": "问卷"}}, "conflicts": [{"field": "x"}]}
        normalized = ensure_trace_structure(trace)
        assert normalized["elements"]["risk_level"]["source"] == "问卷"
        assert normalized["conflicts"] == [{"field": "x"}]

    def test_ensure_trace_structure_drops_invalid_parts(self):
        normalized = ensure_trace_structure({"elements": "bad", "conflicts": {}})
        assert normalized == {"elements": {}, "conflicts": []}

    def test_set_element_trace_writes_and_overwrites(self):
        trace = set_element_trace(None, "risk_level", build_trace_entry("问卷", None, 1))
        assert trace["elements"]["risk_level"]["source"] == "问卷"
        trace = set_element_trace(trace, "risk_level", build_trace_entry("对话", "回撤 20%", 2))
        assert trace["elements"]["risk_level"]["source"] == "对话"

    def test_set_element_trace_does_not_mutate_input(self):
        original = {"elements": {"risk_level": {"source": "问卷"}}, "conflicts": []}
        set_element_trace(original, "investment_horizon", build_trace_entry("问卷", None, 1))
        assert "investment_horizon" not in original["elements"]

    def test_remove_element_trace(self):
        trace = set_element_trace(None, "risk_level", build_trace_entry("问卷", None, 1))
        trace = remove_element_trace(trace, "risk_level")
        assert trace["elements"] == {}

    def test_upsert_conflict_adds_entry(self):
        trace = upsert_conflict(None, "risk_level", "C4", "C1", "对话", "我不想亏钱", 2)
        assert len(trace["conflicts"]) == 1
        conflict = trace["conflicts"][0]
        assert conflict["field"] == "risk_level"
        assert conflict["current"] == "C4"
        assert conflict["proposed"] == "C1"
        assert conflict["source"] == "对话"
        assert conflict["quote"] == "我不想亏钱"
        assert conflict["version"] == 2
        assert conflict["created_at"]

    def test_upsert_conflict_replaces_same_field(self):
        trace = upsert_conflict(None, "risk_level", "C4", "C1", "对话", "a", 2)
        trace = upsert_conflict(trace, "risk_level", "C4", "C2", "对话", "b", 3)
        assert len(trace["conflicts"]) == 1
        assert trace["conflicts"][0]["proposed"] == "C2"

    def test_remove_conflict_by_field(self):
        trace = upsert_conflict(None, "risk_level", "C4", "C1", "对话", "a", 2)
        trace = upsert_conflict(trace, "investment_horizon", "中期", "长期", "对话", "b", 2)
        trace = remove_conflict(trace, "risk_level")
        assert [c["field"] for c in trace["conflicts"]] == ["investment_horizon"]

    def test_clear_conflicts(self):
        trace = upsert_conflict(None, "risk_level", "C4", "C1", "对话", "a", 2)
        trace = clear_conflicts(trace)
        assert trace["conflicts"] == []


class TestIncompleteSources:
    def test_all_missing_for_none_mix(self):
        assert incomplete_sources(None) == ["问卷", "对话", "持仓"]

    def test_only_questionnaire_collected(self):
        assert incomplete_sources({"questionnaire": 1.0}) == ["对话", "持仓"]

    def test_all_collected(self):
        assert incomplete_sources({"questionnaire": 0.4, "dialog": 0.3, "holdings": 0.3}) == []


class TestChartScore:
    """AC-1 雷达评分归一(规则登记于 requirements.md v1.3)。"""

    def test_risk_level_scores_linear(self):
        from app.services.profile_report import RISK_LEVEL_SCORE_MAP

        assert RISK_LEVEL_SCORE_MAP == {
            RiskLevel.C1: 20,
            RiskLevel.C2: 40,
            RiskLevel.C3: 60,
            RiskLevel.C4: 80,
            RiskLevel.C5: 100,
        }

    def test_expectation_score_midpoint(self):
        assert expectation_score(6, 10) == 32  # 中点 8% / 25% * 100
        assert expectation_score(10, 15) == 50  # 中点 12.5%

    def test_expectation_score_capped_at_100(self):
        assert expectation_score(20, 40) == 100  # 中点 30% 超封顶
        assert expectation_score(25, 25) == 100

    def test_expectation_score_null_when_missing(self):
        assert expectation_score(None, None) is None
        assert expectation_score(6, None) is None

    def test_horizon_scores_mapping(self):
        from app.services.profile_report import HORIZON_SCORE_MAP

        assert HORIZON_SCORE_MAP == {"短期": 33, "中期": 67, "长期": 100}

    def test_holding_habit_score_from_top3_share(self):
        assert holding_habit_score(Decimal("0.70")) == 30  # 分散度 = 1 - 前三集中度
        assert holding_habit_score(Decimal("0.30")) == 70

    def test_holding_habit_score_null_without_snapshot(self):
        assert holding_habit_score(None) is None


class TestBuildDimensions:
    def test_four_dimensions_with_values_and_scores(self):
        dimensions = build_dimensions(make_profile(), None)
        assert [d["key"] for d in dimensions] == [
            "risk_level",
            "return_expectation",
            "investment_horizon",
            "holding_habit_summary",
        ]
        risk, expectation, horizon, habit = dimensions
        assert risk["label"] == "风险等级"
        assert risk["display"] == "C4" and risk["display_label"] == "进取型"
        assert risk["score"] == 80
        assert expectation["display"] == "6%~10%" and expectation["score"] == 32
        assert horizon["display"] == "中期" and horizon["score"] == 67
        assert habit["display"] == "持仓 3 只;集中度高。" and habit["score"] is None
        assert habit["score_updated_at"] is None

    def test_trace_provenance_attached(self):
        trace = set_element_trace(None, "risk_level", build_trace_entry("问卷", None, 1))
        trace = set_element_trace(trace, "return_expectation", build_trace_entry("对话", "希望年化 6% 到 10%", 2))
        dimensions = build_dimensions(make_profile(), trace)
        risk = dimensions[0]
        assert risk["source"] == "问卷" and risk["quote"] is None
        assert risk["source_version"] == 1 and risk["updated_at"]
        expectation = dimensions[1]
        assert expectation["source"] == "对话"
        assert expectation["quote"] == "希望年化 6% 到 10%"
        assert expectation["source_version"] == 2
        # 无 trace 的要素来源为 null(存量画像兼容)
        assert dimensions[2]["source"] is None

    def test_legacy_profile_without_trace_null_sources(self):
        dimensions = build_dimensions(make_profile(source_trace=None), None)
        assert all(d["source"] is None and d["quote"] is None for d in dimensions)

    def test_holding_habit_score_updated_at_from_holdings_info(self):
        holdings_info = {"top3_share": Decimal("0.60"), "updated_at": "2026-09-21T09:00:00+00:00"}
        habit = build_dimensions(make_profile(), None, holdings_info)[3]
        assert habit["score"] == 40
        assert habit["score_updated_at"] == "2026-09-21T09:00:00+00:00"

    def test_missing_values_render_null(self):
        profile = make_profile(
            return_expectation_low=None, return_expectation_high=None, investment_horizon=None,
            holding_habit_summary=None,
        )
        expectation, horizon, habit = build_dimensions(profile, None)[1:]
        assert expectation["display"] is None and expectation["score"] is None
        assert horizon["display"] is None and horizon["score"] is None
        assert habit["display"] is None and habit["score"] is None


class TestSerializeProfileCompact:
    def test_json_native_types(self):
        view = serialize_profile_compact(make_profile())
        assert view["risk_level"] == "C4" and view["risk_level_name"] == "进取型"
        assert view["return_expectation_low"] == 6.0  # Decimal → float(缓存序列化安全)
        assert view["return_expectation_high"] == 10.0
        assert view["investment_horizon"] == "中期"
        assert view["holding_habit_summary"] == "持仓 3 只;集中度高。"
        assert view["source_mix"] == {"questionnaire": 0.5, "dialog": 0.5}
        assert view["confidence"] == 0.85
        assert view["confirmed"] is False
        assert view["version"] == 2

    def test_none_fields_kept(self):
        view = serialize_profile_compact(
            make_profile(holding_habit_summary=None, confidence=None)
        )
        assert view["holding_habit_summary"] is None
        assert view["confidence"] is None
