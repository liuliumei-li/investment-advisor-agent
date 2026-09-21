"""画像报告纯函数单测(US-04):逐要素溯源读写与待确认冲突生命周期。"""

from app.services.profile_report import (
    build_trace_entry,
    clear_conflicts,
    ensure_trace_structure,
    incomplete_sources,
    remove_conflict,
    remove_element_trace,
    set_element_trace,
    upsert_conflict,
)


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
