"""画像业务编排:问卷作答 → 风险等级与画像要素(BR-IMG-01/02/03,UC-01)。

仅做编排:评分规则在 risk_scoring,数据访问在 Repository,缓存经 app/cache。
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import Cache, profile_cache_key
from app.core.exceptions import NotFound, ValidationFailed
from app.models.questionnaire_response import QuestionnaireResponse
from app.models.user_profile import RISK_LEVEL_LABELS, RiskLevel, UserProfile
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.risk_scoring import evaluate_answers, extract_profile_fields

# 三来源仅问卷一种已收集时的画像置信度(UC-01 扩展 2a:允许先以低置信度画像继续)
QUESTIONNAIRE_ONLY_CONFIDENCE = Decimal("0.60")

# BR-IMG-03:仅问卷单一来源时,提示其余来源信息不完整
INCOMPLETE_SOURCES = ["对话", "持仓"]


class ProfileService:
    def __init__(
        self,
        questionnaire_repo: QuestionnaireRepository,
        profile_repo: ProfileRepository,
        session: AsyncSession,
        cache: Cache,
    ):
        self.questionnaire_repo = questionnaire_repo
        self.profile_repo = profile_repo
        self.session = session
        self.cache = cache

    async def get_latest_questionnaire(self) -> dict | None:
        questionnaire = await self.questionnaire_repo.get_latest()
        if questionnaire is None:
            return None
        return {
            "id": questionnaire.id,
            "title": questionnaire.title,
            "version": questionnaire.version,
            "questions": questionnaire.questions,
        }

    async def submit_questionnaire(self, user_id: int, questionnaire_id: int, answers: list[dict]) -> dict:
        questionnaire = await self.questionnaire_repo.get_latest()
        if questionnaire is None or questionnaire.id != questionnaire_id:
            raise NotFound("问卷不存在或已更新,请获取最新问卷")
        validate_answers(questionnaire.questions, answers)
        dimension_scores, total, risk_level = evaluate_answers(questionnaire.questions, answers)
        fields = extract_profile_fields(questionnaire.questions, answers)

        await self.questionnaire_repo.create_response(
            QuestionnaireResponse(
                user_id=user_id,
                questionnaire_id=questionnaire.id,
                answers=answers,
                score=total,
                risk_level=risk_level,
            )
        )

        profile = await self.profile_repo.get_by_user_id(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id, version=1)
        else:
            profile.version += 1  # BR-IMG-06:画像更新以版本递增留痕
        profile.risk_level = risk_level
        profile.return_expectation_low = _as_decimal(fields["return_expectation_low"])
        profile.return_expectation_high = _as_decimal(fields["return_expectation_high"])
        profile.investment_horizon = fields["investment_horizon"]
        profile.holding_habit_summary = None  # 持仓习惯来自 US-03,问卷阶段为空
        profile.source_mix = {"questionnaire": 1.0}
        profile.confidence = QUESTIONNAIRE_ONLY_CONFIDENCE
        profile.confirmed = False  # BR-IMG-05:确认/修正属 US-04 流程
        await self.profile_repo.save(profile)
        await self.session.commit()
        await self.cache.delete(profile_cache_key(user_id))
        return _submit_result(questionnaire.id, total, risk_level, dimension_scores, fields, profile.version)

    async def get_latest_response(self, user_id: int) -> dict | None:
        response = await self.questionnaire_repo.get_latest_response(user_id)
        if response is None:
            return None
        return {
            "id": response.id,
            "questionnaire_id": response.questionnaire_id,
            "score": response.score,
            "risk_level": response.risk_level.value,
            "risk_level_name": RISK_LEVEL_LABELS[response.risk_level],
            "answers": response.answers,
            "created_at": response.created_at.isoformat(),
        }


def validate_answers(questions: list[dict], answers: list[dict]) -> None:
    """作答完整性校验:每题恰好一个答案,题目与选项必须存在。"""
    question_ids = {q["id"] for q in questions}
    seen: set[str] = set()
    for answer in answers:
        question_id = answer["question_id"]
        if question_id not in question_ids:
            raise ValidationFailed(f"无效的题目:{question_id}")
        if question_id in seen:
            raise ValidationFailed(f"题目重复作答:{question_id}")
        seen.add(question_id)
        question = next(q for q in questions if q["id"] == question_id)
        if answer["option_id"] not in {o["id"] for o in question["options"]}:
            raise ValidationFailed(f"题目 {question_id} 的选项无效")
    missing = question_ids - seen
    if missing:
        raise ValidationFailed(f"作答不完整,缺少题目:{','.join(sorted(missing))}")


def _as_decimal(value) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _to_float(value) -> float | None:
    return float(value) if value is not None else None


def _submit_result(
    questionnaire_id: int,
    total: int,
    risk_level: RiskLevel,
    dimension_scores: dict[str, int],
    fields: dict,
    version: int,
) -> dict:
    return {
        "questionnaire_id": questionnaire_id,
        "score": total,
        "risk_level": risk_level.value,
        "risk_level_name": RISK_LEVEL_LABELS[risk_level],
        "dimension_scores": dimension_scores,
        "profile": {
            "risk_level": risk_level.value,
            "risk_level_name": RISK_LEVEL_LABELS[risk_level],
            "return_expectation_low": _to_float(fields["return_expectation_low"]),
            "return_expectation_high": _to_float(fields["return_expectation_high"]),
            "investment_horizon": fields["investment_horizon"],
            "holding_habit_summary": None,
            "source_mix": {"questionnaire": 1.0},
            "confidence": float(QUESTIONNAIRE_ONLY_CONFIDENCE),
            "confirmed": False,
            "version": version,
        },
        "incomplete_sources": INCOMPLETE_SOURCES,
    }
