"""US-02 对话画像单测:抽取校验(原文引用防幻觉)、追问策略、画像合并与冲突(AC-1~AC-4)。"""

from decimal import Decimal

import pytest

from app.core.exceptions import ValidationFailed
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.models.user_profile import RiskLevel, UserProfile
from app.repositories.profile_repo import ProfileRepository
from app.repositories.profile_update_repo import ProfileUpdateRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.dialog_profile_service import DialogExtraction, DialogProfileService, validate_extraction
from app.services.profile_service import ProfileService, build_dialog_updates
from tests.helpers import FakeLLM, full_answers

QUESTIONS = QUESTIONNAIRE_V1["questions"]

RISK = {"risk_tolerance": {"level": "C3", "evidence": "我能承受 20% 回撤"}}
EXPECTATION = {"return_expectation": {"low": 10, "high": 15, "evidence": "希望年化 10% 到 15%"}}
HORIZON = {"investment_horizon": {"value": "长期", "evidence": "打算放三年"}}
HABIT = {"holding_habit": {"summary": "重仓白酒", "evidence": "我主要拿的是白酒股"}}

USER_TEXT = "我能承受 20% 回撤,希望年化 10% 到 15%,打算放三年,我主要拿的是白酒股"


def make_profile_service(session, cache) -> ProfileService:
    return ProfileService(QuestionnaireRepository(session), ProfileRepository(session), session, cache)


def make_dialog_service(session, cache, llm) -> DialogProfileService:
    return DialogProfileService(make_profile_service(session, cache), llm, cache)


def extraction(**overrides) -> DialogExtraction:
    data = {**RISK, **EXPECTATION, **HORIZON, **HABIT, **overrides}
    return DialogExtraction(**data)


class TestValidateExtraction:
    def test_all_valid_slots_pass(self):
        result = validate_extraction(extraction(), [USER_TEXT])
        assert result.risk_tolerance.level == "C3"
        assert result.return_expectation.low == 10
        assert result.investment_horizon.value == "长期"
        assert result.holding_habit.summary == "重仓白酒"

    def test_fabricated_evidence_dropped(self):
        result = validate_extraction(
            extraction(risk_tolerance={"level": "C3", "evidence": "我完全没说过的话"}), [USER_TEXT]
        )
        assert result.risk_tolerance is None
        # 其余槽位不受影响
        assert result.return_expectation is not None

    def test_evidence_whitespace_tolerant(self):
        result = validate_extraction(
            extraction(risk_tolerance={"level": "C3", "evidence": "我能承受20%回撤"}), [USER_TEXT]
        )
        assert result.risk_tolerance is not None

    def test_invalid_level_dropped(self):
        result = validate_extraction(
            extraction(risk_tolerance={"level": "C9", "evidence": "我能承受 20% 回撤"}), [USER_TEXT]
        )
        assert result.risk_tolerance is None

    def test_inverted_range_dropped(self):
        result = validate_extraction(
            extraction(return_expectation={"low": 20, "high": 5, "evidence": "希望年化 10% 到 15%"}), [USER_TEXT]
        )
        assert result.return_expectation is None

    def test_out_of_bound_return_dropped(self):
        result = validate_extraction(
            extraction(return_expectation={"low": 10, "high": 500, "evidence": "希望年化 10% 到 15%"}), [USER_TEXT]
        )
        assert result.return_expectation is None

    def test_invalid_horizon_dropped(self):
        result = validate_extraction(
            extraction(investment_horizon={"value": "五年", "evidence": "打算放三年"}), [USER_TEXT]
        )
        assert result.investment_horizon is None


class TestClarificationFlow:
    async def test_missing_slots_trigger_two_questions_in_priority_order(self, db_session, cache):
        service = make_dialog_service(db_session, cache, FakeLLM([{}]))
        result = await service.process_message(1, None, "先聊聊吧")
        assert result["needs_clarification"] is True
        assert result["completed"] is False
        assert "回撤" in result["reply"]
        assert "年化收益率" in result["reply"]

    async def test_garbage_llm_output_treated_as_empty(self, db_session, cache):
        service = make_dialog_service(db_session, cache, FakeLLM([{"risk_tolerance": {"level": "C4"}}]))
        result = await service.process_message(1, None, "我想稳一点")
        assert result["needs_clarification"] is True
        assert result["slots"] == {}

    async def test_context_persists_across_rounds(self, db_session, cache):
        llm = FakeLLM(
            [
                {"risk_tolerance": {"level": "C3", "evidence": "我能承受 20% 回撤"}},
                {**EXPECTATION, **HORIZON},
            ]
        )
        service = make_dialog_service(db_session, cache, llm)
        first = await service.process_message(1, None, "我能承受 20% 回撤")
        assert first["needs_clarification"] is True
        assert first["slots"]["risk_tolerance"]["level"] == "C3"

        second = await service.process_message(1, first["session_id"], "希望年化 10% 到 15%,打算放三年")
        assert second["completed"] is True
        assert len(llm.calls) == 2
        # 第二轮抽取携带完整历史,追问上下文中含首轮用户原话
        history = llm.calls[1]
        assert any(m["role"] == "user" and "20% 回撤" in m["content"] for m in history)

    async def test_finish_without_enough_info_and_no_profile_raises(self, db_session, cache):
        service = make_dialog_service(db_session, cache, FakeLLM([]))
        with pytest.raises(ValidationFailed, match="风险承受信息不足"):
            await service.process_message(1, None, "就这些吧", finish=True)


class TestProcessMessageCompletion:
    async def test_two_round_completion_creates_dialog_only_profile(self, db_session, cache):
        llm = FakeLLM(
            [
                {"risk_tolerance": {"level": "C4", "evidence": "回撤 30% 也能接受"}},
                {**EXPECTATION, **HORIZON, **HABIT},
            ]
        )
        service = make_dialog_service(db_session, cache, llm)
        first = await service.process_message(1, None, "回撤 30% 也能接受")
        result = await service.process_message(
            1, first["session_id"], "希望年化 10% 到 15%,打算放三年,我主要拿的是白酒股"
        )
        assert result["completed"] is True
        assert result["needs_clarification"] is False
        updates = result["profile_updates"]
        assert {u["field"] for u in updates} == {
            "risk_level",
            "return_expectation",
            "investment_horizon",
            "holding_habit_summary",
        }
        assert all(u["applied"] and u["quote"] for u in updates)  # AC-4:每项带原文引用
        # 会话上下文已清理
        from app.cache.redis_client import session_ctx_key

        assert await cache.get_json(session_ctx_key(first["session_id"])) is None

        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.risk_level == RiskLevel.C4
        assert profile.source_mix == {"dialog": 1.0}
        assert float(profile.confidence) == 0.55

    async def test_rounds_cap_finalizes_with_partial_slots(self, db_session, cache, seeded_questionnaire):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        llm = FakeLLM([{}, {}, {}, {}, {}])
        service = make_dialog_service(db_session, cache, llm)
        result = None
        for message in ["你好", "好的", "嗯", "行", "可以"]:
            result = await service.process_message(1, None if result is None else result["session_id"], message)
        assert result["completed"] is True
        assert result["profile_updates"] == []  # 无有效抽取,画像不动


class TestMergeDialogFields:
    async def test_new_user_requires_risk_tolerance(self, db_session, cache):
        service = make_profile_service(db_session, cache)
        with pytest.raises(ValidationFailed, match="风险承受信息不足"):
            await service.merge_dialog_fields(1, {**EXPECTATION, **HORIZON})

    async def test_conflict_keeps_questionnaire_value(self, db_session, cache, seeded_questionnaire):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))  # 全 c 选项 → C4
        # 对话声称 C1,与问卷冲突 → 保留 C4 并记录冲突;holding_habit 无冲突则采纳
        slots = {
            "risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"},
            "holding_habit": {"summary": "重仓白酒", "evidence": "我主要拿的是白酒股"},
        }
        result = await profile_service.merge_dialog_fields(1, slots)
        risk_update = next(u for u in result["profile_updates"] if u["field"] == "risk_level")
        habit_update = next(u for u in result["profile_updates"] if u["field"] == "holding_habit_summary")
        assert risk_update["applied"] is False and risk_update["conflict"] is True
        assert risk_update["before"] == "C4" and risk_update["after"] == "C1"
        assert habit_update["applied"] is True

        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.risk_level == RiskLevel.C4  # 冲突保留原值(AC-3 不覆盖丢失)
        assert profile.holding_habit_summary == "重仓白酒"
        assert profile.source_mix == {"questionnaire": 0.5, "dialog": 0.5}
        assert float(profile.confidence) == 0.85
        assert profile.version == 2  # BR-IMG-06 版本留痕
        # US-04 溯源:冲突持久化供画像报告披露;采纳的持仓习惯写入对话来源 trace
        trace = profile.source_trace
        conflicts = trace["conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["field"] == "risk_level"
        assert conflicts[0]["current"] == "C4" and conflicts[0]["proposed"] == "C1"
        assert conflicts[0]["quote"] == "我完全不想亏钱"
        habit = trace["elements"]["holding_habit_summary"]
        assert habit["source"] == "对话"
        assert habit["quote"] == "我主要拿的是白酒股"

    async def test_applied_updates_write_trace_with_quote_and_conflict_upserted(
        self, db_session, cache, seeded_questionnaire
    ):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))  # C4
        # 首轮冲突(C1);次轮再主张(C2)→ 同一字段只保留最新一条冲突
        await profile_service.merge_dialog_fields(
            1, {"risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"}}
        )
        await profile_service.merge_dialog_fields(
            1, {"risk_tolerance": {"level": "C2", "evidence": "回撤最多 10% 到 20%"}}
        )
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        conflicts = profile.source_trace["conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["proposed"] == "C2"
        assert conflicts[0]["quote"] == "回撤最多 10% 到 20%"
        assert profile.risk_level == RiskLevel.C4  # 冲突始终保留原值

    async def test_conflict_round_records_history_event(self, db_session, cache, seeded_questionnaire):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))  # C4
        await profile_service.merge_dialog_fields(
            1,
            {
                "risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"},
                "holding_habit": {"summary": "重仓白酒", "evidence": "我主要拿的是白酒股"},
            },
        )
        # US-05 AC-3:冲突轮也留痕——因何(对话更新)、采纳项与冲突主张分别记录
        events = await ProfileUpdateRepository(db_session).list_for_user(1, 0, 10)
        assert len(events) == 2
        dialog_event = events[0]
        assert dialog_event.version == 2
        assert dialog_event.trigger == "对话更新"
        assert dialog_event.changes == [
            {
                "field": "holding_habit_summary",
                "before": None,
                "after": "重仓白酒",
                "source": "对话",
                "quote": "我主要拿的是白酒股",
            }
        ]
        assert dialog_event.conflicts == [
            {
                "field": "risk_level",
                "current": "C4",
                "proposed": "C1",
                "source": "对话",
                "quote": "我完全不想亏钱",
            }
        ]

    async def test_consistent_dialog_merges_without_conflict(self, db_session, cache, seeded_questionnaire):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        # 对话表述与问卷结论一致(全 c 选项 → C4、6~10、中期)
        slots = {
            "risk_tolerance": {"level": "C4", "evidence": "回撤 30% 也能接受"},
            "return_expectation": {"low": 6, "high": 10, "evidence": "希望年化 6% 到 10%"},
            "investment_horizon": {"value": "中期", "evidence": "大概放两年"},
        }
        result = await profile_service.merge_dialog_fields(1, slots)
        assert all(u["applied"] and not u["conflict"] for u in result["profile_updates"])
        assert result["incomplete_sources"] == ["持仓"]


class TestBuildDialogUpdates:
    def test_empty_profile_all_applied(self):
        profile = UserProfile(user_id=1)
        updates = build_dialog_updates(profile, {**RISK, **EXPECTATION, **HORIZON})
        assert all(u["applied"] for u in updates)

    def test_matching_values_applied_without_change(self):
        profile = UserProfile(
            user_id=1,
            risk_level=RiskLevel.C3,
            return_expectation_low=Decimal("10"),
            return_expectation_high=Decimal("15"),
            investment_horizon="长期",
        )
        updates = build_dialog_updates(profile, {**RISK, **EXPECTATION, **HORIZON})
        assert all(u["applied"] and u["after"] == u["before"] for u in updates)

    def test_conflicting_values_flagged(self):
        profile = UserProfile(user_id=1, risk_level=RiskLevel.C2)
        updates = build_dialog_updates(profile, RISK)
        assert updates[0]["applied"] is False
        assert updates[0]["conflict"] is True
        assert updates[0]["before"] == "C2" and updates[0]["after"] == "C3"
