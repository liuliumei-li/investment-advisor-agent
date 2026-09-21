"""持仓结论并入画像单测(US-03 Task 4):merge_holdings_fields 合并与偏差提示规则。"""

from decimal import Decimal

from app.models.user_profile import RiskLevel, UserProfile
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.profile_service import ProfileService


def make_service(session, cache):
    return ProfileService(QuestionnaireRepository(session), ProfileRepository(session), session, cache)


class TestMergeHoldingsFields:
    async def test_new_profile_initialized_from_holdings(self, db_session, cache):
        service = make_service(db_session, cache)
        result = await service.merge_holdings_fields(
            1,
            holding_habit_summary="持仓 3 只;集中度高。",
            inferred_risk_level=RiskLevel.C5,
            stock_share=Decimal("0.9"),
        )
        updates = {u["field"]: u for u in result["profile_updates"]}
        assert updates["risk_level"]["after"] == "C5"
        assert updates["holding_habit_summary"]["applied"] is True
        assert result["risk_deviation"] is None  # 新画像,无自评可比
        assert set(result["incomplete_sources"]) == {"问卷", "对话"}

        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.risk_level is RiskLevel.C5
        assert profile.source_mix == {"holdings": 1.0}
        assert float(profile.confidence) == 0.50
        assert profile.version == 1

    async def test_existing_profile_updates_summary_and_version(self, db_session, cache):
        service = make_service(db_session, cache)
        await ProfileRepository(db_session).save(UserProfile(user_id=1, risk_level=RiskLevel.C2, version=1))
        result = await service.merge_holdings_fields(
            1, holding_habit_summary="持仓 5 只。", inferred_risk_level=RiskLevel.C3, stock_share=Decimal("0.5")
        )
        assert all(u["applied"] for u in result["profile_updates"])
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.version == 2
        assert profile.holding_habit_summary == "持仓 5 只。"
        assert profile.source_mix == {"holdings": 1.0}
        assert float(profile.confidence) == 0.50

    async def test_deviation_prompted_when_gap_two_and_keeps_self_assessed(self, db_session, cache):
        service = make_service(db_session, cache)
        await ProfileRepository(db_session).save(
            UserProfile(user_id=1, risk_level=RiskLevel.C2, version=1, source_mix={"questionnaire": 1.0})
        )
        result = await service.merge_holdings_fields(
            1, holding_habit_summary="全仓股票。", inferred_risk_level=RiskLevel.C4, stock_share=Decimal("0.7")
        )
        deviation = result["risk_deviation"]
        assert deviation["assessed_risk_level"] == "C2"
        assert deviation["portfolio_risk_level"] == "C4"
        assert "偏差明显" in deviation["message"]
        # 自评等级不被持仓覆盖
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.risk_level is RiskLevel.C2
        # 两来源:均分 0.5/0.5,置信度 0.85
        assert profile.source_mix == {"questionnaire": 0.5, "holdings": 0.5}
        assert float(profile.confidence) == 0.85

    async def test_no_deviation_within_gap_one(self, db_session, cache):
        service = make_service(db_session, cache)
        await ProfileRepository(db_session).save(UserProfile(user_id=1, risk_level=RiskLevel.C3, version=1))
        result = await service.merge_holdings_fields(
            1, holding_habit_summary="股票为主。", inferred_risk_level=RiskLevel.C4, stock_share=Decimal("0.7")
        )
        assert result["risk_deviation"] is None

    async def test_three_sources_confidence_095(self, db_session, cache):
        service = make_service(db_session, cache)
        await ProfileRepository(db_session).save(
            UserProfile(
                user_id=1,
                risk_level=RiskLevel.C2,
                version=1,
                source_mix={"questionnaire": 0.5, "dialog": 0.5},
                confidence=Decimal("0.85"),
            )
        )
        result = await service.merge_holdings_fields(
            1, holding_habit_summary="三来源齐备。", inferred_risk_level=RiskLevel.C2, stock_share=Decimal("0.3")
        )
        assert result["incomplete_sources"] == []
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.source_mix == {"questionnaire": 0.3333, "dialog": 0.3333, "holdings": 0.3333}
        assert float(profile.confidence) == 0.95

    async def test_conflict_keeps_original_summary_and_persists_for_report(self, db_session, cache):
        service = make_service(db_session, cache)
        await ProfileRepository(db_session).save(
            UserProfile(
                user_id=1,
                risk_level=RiskLevel.C2,
                version=1,
                holding_habit_summary="长期持有蓝筹(对话来源)",
                source_mix={"dialog": 1.0},
            )
        )
        result = await service.merge_holdings_fields(
            1, holding_habit_summary="频繁换手,重仓个股。", inferred_risk_level=RiskLevel.C5, stock_share=Decimal("0.9")
        )
        update = next(u for u in result["profile_updates"] if u["field"] == "holding_habit_summary")
        assert update["conflict"] is True and update["applied"] is False
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.holding_habit_summary == "长期持有蓝筹(对话来源)"  # 保留原值待确认
        # US-04:冲突持久化进 source_trace,供画像报告披露;冲突轮也递增版本留痕(BR-IMG-06)
        assert profile.version == 2
        conflicts = profile.source_trace["conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["field"] == "holding_habit_summary"
        assert conflicts[0]["current"] == "长期持有蓝筹(对话来源)"
        assert conflicts[0]["proposed"] == "频繁换手,重仓个股。"
        assert conflicts[0]["source"] == "持仓"
