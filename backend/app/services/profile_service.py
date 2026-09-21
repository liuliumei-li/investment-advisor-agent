"""画像业务编排:问卷作答 / 对话合并 / 持仓合并 → 风险等级与画像要素(BR-IMG-01/02/03,UC-01)。

仅做编排:评分规则在 risk_scoring,数据访问在 Repository,缓存经 app/cache。
US-04:各来源采纳/冲突的逐要素溯源(source/quote/version/时间戳)写入 user_profiles.source_trace,
待确认冲突保留披露,由画像报告展示、用户确认或修正(BR-IMG-05、BR-DAT-04)。
US-05:每次版本递增同步写入 profile_update_events 更新历史(何时/因何/哪一要素变化,BR-IMG-06),
事件构建纯函数在 profile_history。
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import Cache, profile_cache_key
from app.core.exceptions import NotFound, ValidationFailed
from app.models.profile_update_event import ProfileUpdateEvent
from app.models.questionnaire_response import QuestionnaireResponse
from app.models.user_profile import RISK_LEVEL_LABELS, RiskLevel, UserProfile
from app.repositories.profile_repo import ProfileRepository
from app.repositories.profile_update_repo import ProfileUpdateRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.holdings_analysis import risk_deviation
from app.services.profile_history import (
    TRIGGER_DIALOG,
    TRIGGER_HOLDINGS,
    TRIGGER_QUESTIONNAIRE,
    build_update_event,
)
from app.services.profile_report import (
    TRACE_SOURCE_DIALOG,
    TRACE_SOURCE_HOLDINGS,
    TRACE_SOURCE_QUESTIONNAIRE,
    build_trace_entry,
    ensure_trace_structure,
    incomplete_sources,
    remove_conflict,
    set_element_trace,
    upsert_conflict,
)
from app.services.risk_scoring import evaluate_answers, extract_profile_fields

# 三来源仅问卷一种已收集时的画像置信度(UC-01 扩展 2a:允许先以低置信度画像继续)
QUESTIONNAIRE_ONLY_CONFIDENCE = Decimal("0.60")

# 对话来源置信度(US-02):仅对话 0.55(定性表述弱于结构化问卷);多来源时与问卷/持仓共用
# 两来源 0.85 / 三来源 0.95(_confidence_for_sources,requirements.md v1.3)
DIALOG_ONLY_CONFIDENCE = Decimal("0.55")

# 持仓来源置信度(US-03):仅持仓 0.50(客观数据但解读有限);两来源 0.85;三来源 0.95
HOLDINGS_ONLY_CONFIDENCE = Decimal("0.50")
TWO_SOURCE_CONFIDENCE = Decimal("0.85")
THREE_SOURCE_CONFIDENCE = Decimal("0.95")

# 问卷提交覆盖的画像要素(结构化重测权威覆盖;重提时删除对应待确认冲突,US-04)
QUESTIONNAIRE_ELEMENT_FIELDS = ["risk_level", "return_expectation", "investment_horizon"]


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
        is_new = profile is None
        if is_new:
            profile = UserProfile(user_id=user_id, version=1)
        else:
            profile.version += 1  # BR-IMG-06:画像更新以版本递增留痕
        # US-05 AC-3:更新历史(变更前值,供事件记录;问卷权威覆盖三要素)
        questionnaire_updates = [
            {
                "field": "risk_level",
                "before": profile.risk_level.value if profile.risk_level is not None else None,
                "after": risk_level.value,
                "source": TRACE_SOURCE_QUESTIONNAIRE,
            },
            {
                "field": "return_expectation",
                "before": [_to_float(profile.return_expectation_low), _to_float(profile.return_expectation_high)]
                if profile.return_expectation_low is not None
                else None,
                "after": [_to_float(fields["return_expectation_low"]), _to_float(fields["return_expectation_high"])],
                "source": TRACE_SOURCE_QUESTIONNAIRE,
            },
            {
                "field": "investment_horizon",
                "before": profile.investment_horizon,
                "after": fields["investment_horizon"],
                "source": TRACE_SOURCE_QUESTIONNAIRE,
            },
        ]
        profile.risk_level = risk_level
        profile.return_expectation_low = _as_decimal(fields["return_expectation_low"])
        profile.return_expectation_high = _as_decimal(fields["return_expectation_high"])
        profile.investment_horizon = fields["investment_horizon"]
        if is_new:
            profile.holding_habit_summary = None  # 持仓习惯来自 US-03,仅新建画像时为空
        # BR-IMG-03:重提问卷不覆盖持仓来源结论;来源集合并入问卷后均分权重
        mix = {"questionnaire": 1.0}
        if not is_new:
            mix = dict(profile.source_mix or {})
            mix["questionnaire"] = 0.0
            weight = round(1.0 / len(mix), 4)
            mix = {key: weight for key in mix}
        profile.source_mix = mix
        profile.confidence = (
            QUESTIONNAIRE_ONLY_CONFIDENCE if len(mix) == 1 else _confidence_for_sources(len(mix))
        )
        profile.confirmed = False  # BR-IMG-05:确认/修正属 US-04 流程
        # BR-DAT-04 溯源:问卷贡献三要素;重提覆盖的要素删除对应待确认冲突
        trace = ensure_trace_structure(profile.source_trace)
        for field in QUESTIONNAIRE_ELEMENT_FIELDS:
            entry = build_trace_entry(TRACE_SOURCE_QUESTIONNAIRE, None, profile.version)
            trace = set_element_trace(trace, field, entry)
            trace = remove_conflict(trace, field)
        profile.source_trace = trace
        # US-05 AC-3:更新历史(何时/因何/哪一要素,BR-IMG-06),与画像同事务落库
        event = ProfileUpdateEvent(
            **build_update_event(user_id, profile.version, TRIGGER_QUESTIONNAIRE, questionnaire_updates)
        )
        await ProfileUpdateRepository(self.session).add(event)
        await self.profile_repo.save(profile)
        await self.session.commit()
        await self.cache.delete(profile_cache_key(user_id))
        return _submit_result(
            questionnaire.id,
            total,
            risk_level,
            dimension_scores,
            fields,
            profile.version,
            mix,
            profile.holding_habit_summary,
        )

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
        # 采纳(含与现有一致的同值确认,对话来源已收集,BR-IMG-03)即算贡献;冲突另计
        contributed = [u for u in updates if u["applied"]]
        has_conflicts = any(not u["applied"] for u in updates)
        if contributed or has_conflicts:
            if not is_new:
                profile.version += 1  # BR-IMG-06:画像更新以版本递增留痕
            for update in updates:
                if update["applied"] and update["after"] != update["before"]:
                    _apply_dialog_update(profile, update)
            # BR-IMG-03:多来源综合建模,来源集合均分权重,置信度随来源数提升
            mix = dict(profile.source_mix or {})
            mix["dialog"] = 0.0
            weight = round(1.0 / len(mix), 4)
            profile.source_mix = {key: weight for key in mix}
            profile.confidence = DIALOG_ONLY_CONFIDENCE if len(mix) == 1 else _confidence_for_sources(len(mix))
            profile.confirmed = False  # BR-IMG-05:确认/修正属 US-04 流程
            # BR-DAT-04 溯源:采纳项写入来源与原文引用;冲突项记录待确认冲突(US-04 报告披露)
            trace = ensure_trace_structure(profile.source_trace)
            for update in updates:
                if update["applied"]:
                    if update["after"] != update["before"]:
                        entry = build_trace_entry(TRACE_SOURCE_DIALOG, update["quote"], profile.version)
                        trace = set_element_trace(trace, update["field"], entry)
                else:
                    trace = upsert_conflict(
                        trace,
                        update["field"],
                        update["before"],
                        update["after"],
                        TRACE_SOURCE_DIALOG,
                        update["quote"],
                        profile.version,
                    )
            profile.source_trace = trace
            # US-05 AC-3:更新历史(采纳项与冲突主张分别记录,BR-IMG-06)
            applied_updates = [u for u in updates if u["applied"]]
            conflict_updates = [u for u in updates if not u["applied"]]
            event = ProfileUpdateEvent(
                **build_update_event(user_id, profile.version, TRIGGER_DIALOG, applied_updates, conflict_updates)
            )
            await ProfileUpdateRepository(self.session).add(event)
            await self.profile_repo.save(profile)
            await self.session.commit()
            await self.cache.delete(profile_cache_key(user_id))

        return {"profile_updates": updates, "incomplete_sources": incomplete_sources(profile.source_mix)}

    async def merge_holdings_fields(
        self,
        user_id: int,
        holding_habit_summary: str,
        inferred_risk_level: RiskLevel,
        stock_share: Decimal,
    ) -> dict:
        """持仓分析结论纳入画像(US-03 AC-3)。

        - 无画像时以持仓推导风险等级初始化画像(持仓可作为首个画像来源,UC-01 主流程);
        - 持仓习惯元素级合并:无值或一致则采纳,冲突保留原值待用户确认(BR-IMG-05);
        - 来源权重均分,置信度:单来源 0.50 / 两来源 0.85 / 三来源 0.95;
        - 自评风险等级与持仓实际水平偏差 ≥2 档时返回 risk_deviation 提示(AC-3)。
        """
        profile = await self.profile_repo.get_by_user_id(user_id)
        is_new = profile is None
        updates: list[dict] = []
        if is_new:
            profile = UserProfile(user_id=user_id, version=1, risk_level=inferred_risk_level)
            updates.append(
                {
                    "field": "risk_level",
                    "before": None,
                    "after": inferred_risk_level.value,
                    "source": "持仓",
                    "applied": True,
                    "conflict": False,
                }
            )

        old_summary = profile.holding_habit_summary
        same = old_summary == holding_habit_summary
        if old_summary is None or same:
            profile.holding_habit_summary = holding_habit_summary
        updates.append(
            {
                "field": "holding_habit_summary",
                "before": old_summary,
                "after": holding_habit_summary,
                "source": "持仓",
                "applied": old_summary is None or same,
                "conflict": old_summary is not None and not same,
            }
        )

        contributed = [u for u in updates if u["applied"] and u["after"] != u["before"]]
        has_conflicts = any(not u["applied"] for u in updates)
        if contributed or has_conflicts:
            if not is_new:
                profile.version += 1  # BR-IMG-06:画像更新以版本递增留痕
            mix = dict(profile.source_mix or {})
            mix["holdings"] = 0.0
            present = list(mix)
            weight = round(1.0 / len(present), 4)
            profile.source_mix = {key: weight for key in present}
            profile.confidence = _confidence_for_sources(len(present))
            profile.confirmed = False  # BR-IMG-05:确认/修正属 US-04 流程
            # BR-DAT-04 溯源:无画像时风险等级来自持仓;持仓习惯采纳/冲突记入 trace(US-04 报告披露)
            trace = ensure_trace_structure(profile.source_trace)
            for update in updates:
                if update["applied"]:
                    if update["after"] != update["before"]:
                        trace = set_element_trace(
                            trace, update["field"], build_trace_entry(TRACE_SOURCE_HOLDINGS, None, profile.version)
                        )
                else:
                    trace = upsert_conflict(
                        trace,
                        update["field"],
                        update["before"],
                        update["after"],
                        TRACE_SOURCE_HOLDINGS,
                        None,
                        profile.version,
                    )
            profile.source_trace = trace
            # US-05 AC-3:更新历史(采纳项与冲突主张分别记录,BR-IMG-06)
            applied_updates = [u for u in updates if u["applied"]]
            conflict_updates = [u for u in updates if not u["applied"]]
            event = ProfileUpdateEvent(
                **build_update_event(user_id, profile.version, TRIGGER_HOLDINGS, applied_updates, conflict_updates)
            )
            await ProfileUpdateRepository(self.session).add(event)
            await self.profile_repo.save(profile)
            await self.session.commit()
            await self.cache.delete(profile_cache_key(user_id))

        deviation = None
        if not is_new and risk_deviation(profile.risk_level, inferred_risk_level):
            deviation = {
                "assessed_risk_level": profile.risk_level.value,
                "assessed_risk_level_name": RISK_LEVEL_LABELS[profile.risk_level],
                "portfolio_risk_level": inferred_risk_level.value,
                "portfolio_risk_level_name": RISK_LEVEL_LABELS[inferred_risk_level],
                "stock_share": _to_float(stock_share),
                "message": (
                    f"自评风险等级({RISK_LEVEL_LABELS[profile.risk_level]})与实际持仓风险水平"
                    f"({RISK_LEVEL_LABELS[inferred_risk_level]},股票类资产占比 {float(stock_share) * 100:.1f}%)"
                    "偏差明显,建议在画像报告中确认或修正风险等级"
                ),
            }
        return {
            "profile_updates": updates,
            "incomplete_sources": incomplete_sources(profile.source_mix),
            "risk_deviation": deviation,
        }


def _confidence_for_sources(count: int) -> Decimal:
    """来源数量 → 画像置信度(US-03 补定规则,登记于 requirements.md)。"""
    if count == 1:
        return HOLDINGS_ONLY_CONFIDENCE
    if count == 2:
        return TWO_SOURCE_CONFIDENCE
    return THREE_SOURCE_CONFIDENCE


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
    source_mix: dict,
    holding_habit_summary: str | None,
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
            "holding_habit_summary": holding_habit_summary,  # 重提场景保留持仓来源结论(BR-IMG-03)
            "source_mix": source_mix,
            "confidence": float(
                QUESTIONNAIRE_ONLY_CONFIDENCE if len(source_mix) == 1 else _confidence_for_sources(len(source_mix))
            ),
            "confirmed": False,
            "version": version,
        },
        "incomplete_sources": incomplete_sources(source_mix),
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
