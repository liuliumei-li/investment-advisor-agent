"""ProfileReportService 单测(US-04):报告生成(含溯源)、当前画像缓存、确认/修正(AC-1~AC-4、BR-IMG-05)。"""

from decimal import Decimal

import pytest

from app.cache.redis_client import profile_cache_key
from app.core.exceptions import NotFound, ValidationFailed
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.models.user_profile import RiskLevel, UserProfile
from app.repositories.holding_repo import HoldingRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.holdings_service import HoldingsService
from app.services.profile_report_service import ProfileReportService
from app.services.profile_service import ProfileService
from tests.helpers import full_answers

QUESTIONS = QUESTIONNAIRE_V1["questions"]


def make_report_service(session, cache) -> ProfileReportService:
    return ProfileReportService(
        ProfileRepository(session), HoldingRepository(session), session, cache
    )


def make_profile_service(session, cache) -> ProfileService:
    return ProfileService(QuestionnaireRepository(session), ProfileRepository(session), session, cache)


def make_holdings_service(session, profile_service) -> HoldingsService:
    return HoldingsService(session, HoldingRepository(session), llm=None, profile_service=profile_service)


FOUR_HOLDINGS = [
    {"asset_type": "stock", "code": "600519", "name": "贵州茅台", "quantity": 4, "cost_price": 10},
    {"asset_type": "stock", "code": "000858", "name": "五粮液", "quantity": 3, "cost_price": 10},
    {"asset_type": "etf", "code": "510300", "name": "沪深300ETF", "quantity": 2, "cost_price": 10},
    {"asset_type": "fund", "code": "000001", "name": "某基金", "quantity": 1, "cost_price": 10},
]  # 市值 40/30/20/10 → 前三集中度 0.90 → 持仓习惯轴分数 10


class TestGetCurrent:
    async def test_raises_not_found_without_profile(self, db_session, cache):
        with pytest.raises(NotFound, match="画像不存在"):
            await make_report_service(db_session, cache).get_current(1)

    async def test_returns_compact_view(self, db_session, cache, seeded_questionnaire):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )
        view = await make_report_service(db_session, cache).get_current(1)
        assert view["risk_level"] == "C4" and view["risk_level_name"] == "进取型"
        assert view["return_expectation_low"] == 6.0
        assert view["investment_horizon"] == "中期"
        assert view["confirmed"] is False and view["version"] == 1

    async def test_cache_miss_writes_and_second_call_hits(self, db_session, cache, seeded_questionnaire):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )
        service = make_report_service(db_session, cache)
        first = await service.get_current(1)
        cached = await cache.get_json(profile_cache_key(1))
        assert cached == first  # 缓存内容与响应同形(JSON 原生类型)
        second = await service.get_current(1)
        assert second == first


class TestGetReport:
    async def test_raises_not_found_without_profile(self, db_session, cache):
        with pytest.raises(NotFound, match="画像不存在"):
            await make_report_service(db_session, cache).get_report(1)

    async def test_questionnaire_only_report_with_provenance(
        self, db_session, cache, seeded_questionnaire
    ):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )
        report = await make_report_service(db_session, cache).get_report(1)

        assert report["version"] == 1
        assert report["confirmed"] is False
        assert report["confidence"] == 0.6
        assert report["source_mix"] == {"questionnaire": 1.0}
        assert report["incomplete_sources"] == ["对话", "持仓"]
        assert report["conflicts"] == []
        risk, expectation, horizon, habit = report["dimensions"]
        # AC-2:逐要素溯源(来源 + 版本 + 时间戳)
        assert risk["display"] == "C4" and risk["display_label"] == "进取型"
        assert risk["score"] == 80
        assert risk["source"] == "问卷" and risk["quote"] is None
        assert risk["source_version"] == 1 and risk["updated_at"]
        assert expectation["display"] == "6%~10%" and expectation["score"] == 32
        assert horizon["display"] == "中期" and horizon["score"] == 67
        assert habit["display"] is None and habit["score"] is None

    async def test_dialog_conflict_disclosed_with_quote(self, db_session, cache, seeded_questionnaire):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        await profile_service.merge_dialog_fields(
            1, {"risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"}}
        )
        report = await make_report_service(db_session, cache).get_report(1)
        assert len(report["conflicts"]) == 1
        conflict = report["conflicts"][0]
        assert conflict["field"] == "risk_level"
        assert conflict["current"] == "C4" and conflict["proposed"] == "C1"
        assert conflict["quote"] == "我完全不想亏钱"
        # 冲突保留原值,维度仍展示问卷来源的 C4
        risk = report["dimensions"][0]
        assert risk["display"] == "C4" and risk["source"] == "问卷"

    async def test_holding_habit_score_from_latest_snapshot(
        self, db_session, cache, seeded_questionnaire
    ):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        await make_holdings_service(db_session, profile_service).import_and_analyze(
            1, "list", FOUR_HOLDINGS
        )
        report = await make_report_service(db_session, cache).get_report(1)
        habit = report["dimensions"][3]
        assert habit["display"]  # 摘要文本非空
        assert habit["score"] == 10  # (1 - 0.90) * 100
        assert habit["score_updated_at"]  # 分数时点 = 快照时间
        assert habit["source"] == "持仓"
        assert report["source_mix"] == {"questionnaire": 0.5, "holdings": 0.5}

    async def test_legacy_profile_without_trace_null_sources(self, db_session, cache):
        await ProfileRepository(db_session).save(
            UserProfile(
                user_id=1,
                risk_level=RiskLevel.C2,
                return_expectation_low=Decimal("4"),
                return_expectation_high=Decimal("8"),
                investment_horizon="短期",
                source_mix={"questionnaire": 1.0},
                confidence=Decimal("0.60"),
                version=1,
            )
        )
        await db_session.commit()
        report = await make_report_service(db_session, cache).get_report(1)
        assert report["conflicts"] == []
        assert all(d["source"] is None for d in report["dimensions"])
        assert report["dimensions"][0]["display"] == "C2"


class TestConfirmOrAmend:
    async def test_raises_not_found_without_profile(self, db_session, cache):
        with pytest.raises(NotFound, match="画像不存在"):
            await make_report_service(db_session, cache).confirm_or_amend(1, True, [])

    async def test_empty_request_rejected(self, db_session, cache):
        await ProfileRepository(db_session).save(UserProfile(user_id=1, risk_level=RiskLevel.C2, version=1))
        await db_session.commit()
        with pytest.raises(ValidationFailed, match="至少提供其一"):
            await make_report_service(db_session, cache).confirm_or_amend(1, False, [])

    async def test_duplicate_field_rejected(self, db_session, cache):
        await ProfileRepository(db_session).save(UserProfile(user_id=1, risk_level=RiskLevel.C2, version=1))
        await db_session.commit()
        amendments = [
            {"field": "risk_level", "value": "C3"},
            {"field": "risk_level", "value": "C4"},
        ]
        with pytest.raises(ValidationFailed, match="重复字段"):
            await make_report_service(db_session, cache).confirm_or_amend(1, False, amendments)

    async def test_invalid_field_rejected(self, db_session, cache):
        await ProfileRepository(db_session).save(UserProfile(user_id=1, risk_level=RiskLevel.C2, version=1))
        await db_session.commit()
        with pytest.raises(ValidationFailed, match="不可修正的画像字段"):
            await make_report_service(db_session, cache).confirm_or_amend(
                1, False, [{"field": "username", "value": "x"}]
            )

    async def test_confirm_sets_confirmed_and_clears_conflicts(
        self, db_session, cache, seeded_questionnaire
    ):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        await profile_service.merge_dialog_fields(
            1, {"risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"}}
        )
        service = make_report_service(db_session, cache)
        result = await service.confirm_or_amend(1, True, [])
        assert result["confirmed"] is True
        assert result["applied_amendments"] == []
        assert result["conflicts_remaining"] == []
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.confirmed is True
        assert profile.source_trace["conflicts"] == []
        assert profile.version == 3  # 问卷 v1 → 对话冲突 v2 → 确认 v3

    async def test_reconfirm_without_changes_no_version_bump(self, db_session, cache):
        await ProfileRepository(db_session).save(
            UserProfile(user_id=1, risk_level=RiskLevel.C2, version=1, confirmed=True)
        )
        await db_session.commit()
        result = await make_report_service(db_session, cache).confirm_or_amend(1, True, [])
        assert result["version"] == 1
        assert result["applied_amendments"] == []

    async def test_amend_risk_level_applies_bumps_version_and_writes_trace(
        self, db_session, cache, seeded_questionnaire
    ):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )  # C4
        service = make_report_service(db_session, cache)
        result = await service.confirm_or_amend(
            1, False, [{"field": "risk_level", "value": "C2"}]
        )
        assert result["risk_level"] == "C2"
        assert result["applied_amendments"] == [{"field": "risk_level", "before": "C4", "after": "C2"}]
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.risk_level is RiskLevel.C2
        assert profile.version == 2
        trace = profile.source_trace["elements"]["risk_level"]
        assert trace["source"] == "用户修正"
        assert trace["quote"] is None
        assert trace["version"] == 2

    async def test_amend_same_value_no_bump_keeps_original_trace(
        self, db_session, cache, seeded_questionnaire
    ):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )  # C4
        result = await make_report_service(db_session, cache).confirm_or_amend(
            1, False, [{"field": "risk_level", "value": "C4"}]
        )
        assert result["version"] == 1
        assert result["applied_amendments"] == []
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.source_trace["elements"]["risk_level"]["source"] == "问卷"

    async def test_amend_return_expectation_and_horizon(self, db_session, cache, seeded_questionnaire):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )
        result = await make_report_service(db_session, cache).confirm_or_amend(
            1,
            False,
            [
                {"field": "return_expectation", "value": {"low": 5, "high": 9}},
                {"field": "investment_horizon", "value": "长期"},
            ],
        )
        assert result["return_expectation_low"] == 5.0
        assert result["return_expectation_high"] == 9.0
        assert result["investment_horizon"] == "长期"
        assert [a["field"] for a in result["applied_amendments"]] == [
            "return_expectation",
            "investment_horizon",
        ]
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.version == 2  # 多个修正只递增一次

    async def test_amend_resolves_field_conflict(self, db_session, cache, seeded_questionnaire):
        profile_service = make_profile_service(db_session, cache)
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        await profile_service.merge_dialog_fields(
            1, {"risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"}}
        )
        result = await make_report_service(db_session, cache).confirm_or_amend(
            1, False, [{"field": "risk_level", "value": "C1"}]
        )
        assert result["risk_level"] == "C1"
        assert result["conflicts_remaining"] == []  # 修正即解决该字段冲突

    async def test_amend_and_confirm_combined(self, db_session, cache, seeded_questionnaire):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )
        result = await make_report_service(db_session, cache).confirm_or_amend(
            1, True, [{"field": "investment_horizon", "value": "长期"}]
        )
        assert result["confirmed"] is True
        assert result["investment_horizon"] == "长期"
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.version == 2  # 修正+确认合并一次递增

    async def test_update_invalidates_profile_cache(self, db_session, cache, seeded_questionnaire):
        await make_profile_service(db_session, cache).submit_questionnaire(
            1, seeded_questionnaire, full_answers(QUESTIONS)
        )
        service = make_report_service(db_session, cache)
        await service.get_current(1)  # 预热缓存
        assert await cache.get_json(profile_cache_key(1)) is not None
        await service.confirm_or_amend(1, True, [])
        assert await cache.get_json(profile_cache_key(1)) is None