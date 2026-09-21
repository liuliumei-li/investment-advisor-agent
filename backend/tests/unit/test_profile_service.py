"""ProfileService 单测:问卷提交 → 评分/画像落库、缓存失效、版本递增(US-01、BR-IMG-01/02/06)。"""

import pytest

from app.cache.redis_client import profile_cache_key
from app.core.exceptions import NotFound, ValidationFailed
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.models.user_profile import RiskLevel
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.profile_service import ProfileService, validate_answers
from tests.helpers import full_answers

QUESTIONS = QUESTIONNAIRE_V1["questions"]


def make_service(session, cache) -> ProfileService:
    return ProfileService(
        QuestionnaireRepository(session), ProfileRepository(session), session, cache
    )


class TestValidateAnswers:
    def test_complete_answers_pass(self):
        validate_answers(QUESTIONS, full_answers(QUESTIONS))

    def test_unknown_question_rejected(self):
        answers = full_answers(QUESTIONS) + [{"question_id": "nope", "option_id": "a"}]
        with pytest.raises(ValidationFailed, match="无效的题目:nope"):
            validate_answers(QUESTIONS, answers)

    def test_duplicate_question_rejected(self):
        answers = full_answers(QUESTIONS)
        answers.append(answers[0])
        with pytest.raises(ValidationFailed, match="题目重复作答"):
            validate_answers(QUESTIONS, answers)

    def test_invalid_option_rejected(self):
        answers = full_answers(QUESTIONS)
        answers[0] = {"question_id": "rt1", "option_id": "z"}
        with pytest.raises(ValidationFailed, match="rt1 的选项无效"):
            validate_answers(QUESTIONS, answers)

    def test_missing_question_rejected(self):
        # 问卷末题为 ie3,缺它时提示缺失题目 id
        answers = full_answers(QUESTIONS)[:-1]
        with pytest.raises(ValidationFailed, match="作答不完整,缺少题目:ie3"):
            validate_answers(QUESTIONS, answers)

    def test_empty_answers_rejected(self):
        with pytest.raises(ValidationFailed, match="作答不完整"):
            validate_answers(QUESTIONS, [])


class TestGetLatestQuestionnaire:
    async def test_none_when_not_seeded(self, db_session, cache):
        assert await make_service(db_session, cache).get_latest_questionnaire() is None

    async def test_returns_seeded_questionnaire(self, db_session, cache, seeded_questionnaire):
        result = await make_service(db_session, cache).get_latest_questionnaire()
        assert result["id"] == seeded_questionnaire
        assert result["title"] == "投资风险承受能力测评"
        assert result["version"] == 1
        assert len(result["questions"]) == 14


class TestSubmitQuestionnaire:
    async def test_first_submit_creates_response_and_profile_v1(
        self, db_session, cache, seeded_questionnaire
    ):
        service = make_service(db_session, cache)
        await cache.set_json(profile_cache_key(1), {"stale": True})

        result = await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))

        # 评分与风险等级(全 c:各维度 60 分 → 总分 60 → C4)
        assert result["score"] == 60
        assert result["risk_level"] == "C4"
        assert result["risk_level_name"] == "进取型"
        assert result["dimension_scores"] == {
            "risk_tolerance": 60,
            "return_expectation": 60,
            "investment_horizon": 60,
            "investment_experience": 60,
        }
        # 画像要素:re1=c → 6%~10%;ih1=c → 中期;问卷单一来源置信度 0.60
        assert result["profile"]["return_expectation_low"] == 6.0
        assert result["profile"]["return_expectation_high"] == 10.0
        assert result["profile"]["investment_horizon"] == "中期"
        assert result["profile"]["source_mix"] == {"questionnaire": 1.0}
        assert result["profile"]["confidence"] == 0.6
        assert result["profile"]["confirmed"] is False
        assert result["profile"]["version"] == 1
        assert result["incomplete_sources"] == ["对话", "持仓"]

        # 作答与画像均已落库(AC-2/AC-3)
        response = await service.get_latest_response(1)
        assert response["score"] == 60
        assert response["risk_level"] == "C4"
        assert len(response["answers"]) == 14
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.risk_level is RiskLevel.C4
        assert profile.version == 1

        # 画像缓存已失效(BR-IMG-06 更新即失效)
        assert await cache.get_json(profile_cache_key(1)) is None

    async def test_second_submit_increments_profile_version(
        self, db_session, cache, seeded_questionnaire
    ):
        service = make_service(db_session, cache)
        await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        result = await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))

        assert result["profile"]["version"] == 2
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.version == 2
        # 历史作答保留,最新作答可再查(AC-3)
        assert await service.get_latest_response(1) is not None

    async def test_stale_questionnaire_id_rejected(self, db_session, cache, seeded_questionnaire):
        service = make_service(db_session, cache)
        with pytest.raises(NotFound, match="问卷不存在或已更新"):
            await service.submit_questionnaire(1, seeded_questionnaire + 999, full_answers(QUESTIONS))

    async def test_invalid_answers_rejected_without_writes(
        self, db_session, cache, seeded_questionnaire
    ):
        service = make_service(db_session, cache)
        with pytest.raises(ValidationFailed):
            await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS)[:-1])
        # 校验失败不得落库
        assert await service.get_latest_response(1) is None
        assert await ProfileRepository(db_session).get_by_user_id(1) is None


class TestGetLatestResponse:
    async def test_none_when_no_response(self, db_session, cache):
        assert await make_service(db_session, cache).get_latest_response(1) is None

    async def test_returns_serialized_response(
        self, db_session, cache, seeded_questionnaire
    ):
        service = make_service(db_session, cache)
        await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))

        result = await service.get_latest_response(1)
        assert result["questionnaire_id"] == seeded_questionnaire
        assert result["risk_level_name"] == "进取型"
        assert result["created_at"]  # ISO 时间戳非空


class TestSubmitQuestionnaireTrace:
    """US-04 溯源:问卷提交写入三要素 trace(BR-DAT-04),重提不覆盖持仓来源(BR-IMG-03)。"""

    async def test_first_submit_writes_three_element_traces(self, db_session, cache, seeded_questionnaire):
        service = make_service(db_session, cache)
        await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))

        elements = (await ProfileRepository(db_session).get_by_user_id(1)).source_trace["elements"]
        assert set(elements) == {"risk_level", "return_expectation", "investment_horizon"}
        for entry in elements.values():
            assert entry["source"] == "问卷"
            assert entry["quote"] is None
            assert entry["version"] == 1
            assert entry["updated_at"]

    async def test_resubmit_keeps_holding_summary_and_holdings_mix(
        self, db_session, cache, seeded_questionnaire
    ):
        service = make_service(db_session, cache)
        await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))
        await service.merge_holdings_fields(
            1, holding_habit_summary="持仓 3 只;集中度高。", inferred_risk_level=RiskLevel.C5, stock_share=0.9
        )
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.holding_habit_summary is not None
        assert profile.source_mix == {"questionnaire": 0.5, "holdings": 0.5}

        result = await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))

        # BR-IMG-03:重提问卷不覆盖持仓来源结论,来源集合均分、置信度随来源数提升
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.holding_habit_summary == "持仓 3 只;集中度高。"
        assert profile.source_mix == {"questionnaire": 0.5, "holdings": 0.5}
        assert float(profile.confidence) == 0.85
        assert result["profile"]["holding_habit_summary"] == "持仓 3 只;集中度高。"
        assert result["incomplete_sources"] == ["对话"]
        # 持仓习惯 trace 保持持仓来源,三要素 trace 刷新为问卷
        elements = profile.source_trace["elements"]
        assert elements["holding_habit_summary"]["source"] == "持仓"
        assert elements["risk_level"]["source"] == "问卷"
        assert elements["risk_level"]["version"] == 3

    async def test_resubmit_removes_stale_conflict_for_overwritten_field(
        self, db_session, cache, seeded_questionnaire
    ):
        service = make_service(db_session, cache)
        await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))  # C4
        await service.merge_dialog_fields(
            1, {"risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"}}
        )
        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert len(profile.source_trace["conflicts"]) == 1

        await service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS))

        profile = await ProfileRepository(db_session).get_by_user_id(1)
        assert profile.source_trace["conflicts"] == []
        assert profile.source_trace["elements"]["risk_level"]["source"] == "问卷"
