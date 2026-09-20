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

# 对话来源置信度(US-02):仅对话 0.55(定性表述弱于结构化问卷);问卷+对话 0.85
DIALOG_ONLY_CONFIDENCE = Decimal("0.55")
QUESTIONNAIRE_DIALOG_CONFIDENCE = Decimal("0.85")

# BR-IMG-03:画像建模三来源(不互相覆盖丢失,来源权重见 source_mix)
ALL_PROFILE_SOURCES = ["问卷", "对话", "持仓"]
SOURCE_KEY_TO_LABEL = {"questionnaire": "问卷", "dialog": "对话", "holdings": "持仓"}

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

    async def merge_dialog_fields(self, user_id: int, slots: dict) -> dict:
        """对话抽取要素合并入画像(US-02 AC-3/AC-4)。

        元素级合并:已有值一致则采纳(不互相覆盖丢失);冲突保留原值并记录,
        待用户在画像报告确认/修正(BR-IMG-05)。仅在画像有实际贡献时落库并递增版本。
        """
        profile = await self.profile_repo.get_by_user_id(user_id)
        is_new = profile is None
        if is_new:
            if "risk_tolerance" not in slots:
                raise ValidationFailed("风险承受信息不足,无法建立画像;请继续对话或完成风险测评问卷")
            profile = UserProfile(user_id=user_id, version=1)

        updates = build_dialog_updates(profile, slots)
        contributed = [u for u in updates if u["applied"]]
        if contributed:
            if not is_new:
                profile.version += 1  # BR-IMG-06:画像更新以版本递增留痕
            for update in updates:
                if update["applied"] and update["after"] != update["before"]:
                    _apply_dialog_update(profile, update)
            # BR-IMG-03:多来源综合建模,来源权重均分,置信度提升
            has_questionnaire = bool((profile.source_mix or {}).get("questionnaire"))
            if has_questionnaire:
                profile.source_mix = {"questionnaire": 0.5, "dialog": 0.5}
                profile.confidence = QUESTIONNAIRE_DIALOG_CONFIDENCE
            else:
                profile.source_mix = {"dialog": 1.0}
                profile.confidence = DIALOG_ONLY_CONFIDENCE
            profile.confirmed = False  # BR-IMG-05:确认/修正属 US-04 流程
            await self.profile_repo.save(profile)
            await self.session.commit()
            await self.cache.delete(profile_cache_key(user_id))

        incomplete = [
            label for key, label in SOURCE_KEY_TO_LABEL.items() if key not in (profile.source_mix or {})
        ]
        return {"profile_updates": updates, "incomplete_sources": incomplete}


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

def build_dialog_updates(profile: UserProfile, slots: dict) -> list[dict]:
    """计算对话要素与当前画像的合并 diff(AC-4 预览依据,纯函数便于单测)。

    applied=True 表示采纳(画像无值或与对话值一致);False 表示冲突保留原值。
    """
    updates: list[dict] = []
    risk = slots.get("risk_tolerance")
    if risk:
        level = risk["level"]
        current = profile.risk_level.value if profile.risk_level is not None else None
        updates.append(
            _dialog_update("risk_level", current, level, risk["evidence"], current is None or current == level)
        )
    expectation = slots.get("return_expectation")
    if expectation:
        low, high = float(expectation["low"]), float(expectation["high"])
        cur_low = float(profile.return_expectation_low) if profile.return_expectation_low is not None else None
        cur_high = float(profile.return_expectation_high) if profile.return_expectation_high is not None else None
        same = cur_low is not None and abs(cur_low - low) < 0.01 and abs(cur_high - high) < 0.01
        updates.append(
            _dialog_update(
                "return_expectation", [cur_low, cur_high], [low, high], expectation["evidence"], cur_low is None or same
            )
        )
    horizon = slots.get("investment_horizon")
    if horizon:
        value = horizon["value"]
        applied = profile.investment_horizon is None or profile.investment_horizon == value
        updates.append(
            _dialog_update("investment_horizon", profile.investment_horizon, value, horizon["evidence"], applied)
        )
    habit = slots.get("holding_habit")
    if habit:
        summary = habit["summary"]
        applied = profile.holding_habit_summary is None or profile.holding_habit_summary == summary
        updates.append(
            _dialog_update("holding_habit_summary", profile.holding_habit_summary, summary, habit["evidence"], applied)
        )
    return updates


def _dialog_update(field: str, before, after, quote: str, applied: bool) -> dict:
    return {
        "field": field,
        "before": before,
        "after": after,
        "source": "对话",
        "quote": quote,
        "applied": applied,
        "conflict": not applied,
    }


def _apply_dialog_update(profile: UserProfile, update: dict) -> None:
    field, after = update["field"], update["after"]
    if field == "risk_level":
        profile.risk_level = RiskLevel(after)
    elif field == "return_expectation":
        profile.return_expectation_low = _as_decimal(after[0])
        profile.return_expectation_high = _as_decimal(after[1])
    elif field == "investment_horizon":
        profile.investment_horizon = after
    elif field == "holding_habit_summary":
        profile.holding_habit_summary = after
