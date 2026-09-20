"""对话画像服务(US-02):自然语言抽取画像要素、追问澄清、合并画像(UC-01)。

设计要点(对齐 docs/architecture.md §2.1、§4.4):
- 抽取走 app/llm 适配层,结构化 JSON 输出;每个抽取值必须附用户原文引用(evidence),
  服务端校验引用真实性,校验失败即丢弃并追问(防幻觉,BR-DAT-05 治理思想);
- 多轮上下文存 Redis `session:ctx:{session_id}`(30 分钟 TTL,每轮续期);
- 追问由服务端模板生成(确定性、可测试),每轮最多 2 个,轮数上限 5(UC-01 扩展 2a);
- 画像合并在 profile_service.merge_dialog_fields:元素级合并,冲突保留原值待用户确认(BR-IMG-05)。
"""

import logging
import secrets

from pydantic import BaseModel, ValidationError

from app.cache.redis_client import SESSION_CTX_TTL_SECONDS, Cache, session_ctx_key
from app.core.exceptions import ValidationFailed
from app.llm.base import LLMClient
from app.models.user_profile import RiskLevel
from app.services.profile_service import ProfileService

logger = logging.getLogger(__name__)

MAX_ROUNDS = 5  # 追问轮数上限,超限按已有信息收口(UC-01 扩展 2a)
MAX_QUESTIONS_PER_ROUND = 2

# BR-IMG-01 对话渠道映射:等级 → 可承受回撤阈值(五档定义与问卷评分同源,登记于 requirements.md)
_RISK_LEVELS = {level.value for level in RiskLevel}
_HORIZONS = {"短期", "中期", "长期"}
_RETURN_BOUND = 100.0  # 年化收益率合理边界(|x| ≤ 100%)

_CLARIFY_TEMPLATES = {
    "risk_tolerance": "您能承受多大比例的短期回撤?(例如:最多 10%、20%~30%、或 40% 以上)",
    "return_expectation": "您期望的年化收益率大概是多少?(例如:5%~10%)",
    "investment_horizon": "这笔钱您计划投资多长时间?(例如:1 年以内、1~3 年、3 年以上)",
}
# holding_habit 不主动追问:用户提到持仓才抽取(持仓分析属 US-03)
_CLARIFY_ORDER = ["risk_tolerance", "return_expectation", "investment_horizon"]

SYSTEM_PROMPT = (
    "你是证券投顾系统的用户画像抽取模块。用户用自然语言描述自己的投资偏好与需求,"
    "你的任务是从对话中抽取画像要素,输出 JSON。\n"
    "抽取要素(无依据时对应字段输出 null,严禁编造、严禁推断用户未提及的信息):\n"
    '1. risk_tolerance:风险承受等级,五档定义(BR-IMG-01):'
    "C1 保守型(可承受回撤 <10%)、C2 稳健型(10%~20%)、C3 平衡型(20%~30%)、"
    'C4 进取型(30%~40%)、C5 激进型(>40%);输出 {"level": "C3", "evidence": "用户原话原文片段"}\n'
    '2. return_expectation:年化收益预期百分比区间,输出 {"low": 10, "high": 15, "evidence": "..."}'
    "(仅提到单一数值时 low=high=该值)\n"
    '3. investment_horizon:投资期限,取值仅限"短期/中期/长期",输出 {"value": "长期", "evidence": "..."}\n'
    '4. holding_habit:持仓习惯一句话摘要(仅当用户明确提到持仓时才抽取),输出 {"summary": "...", "evidence": "..."}\n'
    "要求:evidence 必须是用户消息中的原文片段,逐字一致,不得改写;每条要素的输出完全基于用户原话。"
)


class RiskToleranceSlot(BaseModel):
    level: str  # C1~C5
    evidence: str


class ReturnExpectationSlot(BaseModel):
    low: float
    high: float
    evidence: str


class InvestmentHorizonSlot(BaseModel):
    value: str  # 短期/中期/长期
    evidence: str


class HoldingHabitSlot(BaseModel):
    summary: str
    evidence: str


class DialogExtraction(BaseModel):
    """LLM 抽取结果:无依据的要素为 null,禁止编造(AC-1)。"""

    risk_tolerance: RiskToleranceSlot | None = None
    return_expectation: ReturnExpectationSlot | None = None
    investment_horizon: InvestmentHorizonSlot | None = None
    holding_habit: HoldingHabitSlot | None = None


def _normalize(text: str) -> str:
    """逐字比对前去除全部空白,兼容 LLM 引用中的空格差异。"""
    return "".join(text.split())


def _evidence_found(evidence: str, user_messages: list[str]) -> bool:
    normalized = _normalize(evidence)
    if not normalized:
        return False
    return any(normalized in _normalize(message) for message in user_messages)


def validate_extraction(extraction: DialogExtraction, user_messages: list[str]) -> DialogExtraction:
    """逐槽校验:取值合法 + evidence 为用户原文片段;不合法的槽位丢弃(防幻觉)。"""
    data: dict[str, object] = {}
    if extraction.risk_tolerance is not None and extraction.risk_tolerance.level in _RISK_LEVELS:
        if _evidence_found(extraction.risk_tolerance.evidence, user_messages):
            data["risk_tolerance"] = extraction.risk_tolerance
    if extraction.return_expectation is not None:
        slot = extraction.return_expectation
        valid_range = -_RETURN_BOUND <= slot.low <= slot.high <= _RETURN_BOUND
        if valid_range and _evidence_found(slot.evidence, user_messages):
            data["return_expectation"] = slot
    if extraction.investment_horizon is not None and extraction.investment_horizon.value in _HORIZONS:
        if _evidence_found(extraction.investment_horizon.evidence, user_messages):
            data["investment_horizon"] = extraction.investment_horizon
    if extraction.holding_habit is not None and extraction.holding_habit.summary.strip():
        if _evidence_found(extraction.holding_habit.evidence, user_messages):
            data["holding_habit"] = extraction.holding_habit
    return DialogExtraction(**data)


class DialogProfileService:
    def __init__(self, profile_service: ProfileService, llm: LLMClient, cache: Cache):
        self.profile_service = profile_service
        self.llm = llm
        self.cache = cache

    async def process_message(
        self, user_id: int, session_id: str | None, message: str, finish: bool = False
    ) -> dict:
        """处理一轮对话:抽取 → 校验 → 追问或收口合并(AC-1~AC-4)。

        finish=True 时不再抽取,按已有槽位直接合并(用户主动结束)。
        """
        if not finish and not message.strip():
            raise ValidationFailed("消息内容不能为空")
        ctx = await self._load_ctx(session_id)
        if not finish:
            ctx["messages"].append({"role": "user", "content": message.strip()})
            ctx["rounds"] += 1
            extraction = await self._extract(ctx["messages"])
            user_texts = [m["content"] for m in ctx["messages"] if m["role"] == "user"]
            validated = validate_extraction(extraction, user_texts)
            ctx["slots"] = self._merge_slots(ctx["slots"], validated)

        slots = ctx["slots"]
        if not (finish or self._must_slots_complete(slots) or ctx["rounds"] >= MAX_ROUNDS):
            questions = self._clarification_questions(slots)
            reply = "好的,还想再了解几点:\n" + "\n".join(f"{i + 1}. {q}" for i, q in enumerate(questions))
            await self._save_ctx(ctx)
            return {
                "session_id": ctx["session_id"],
                "reply": reply,
                "needs_clarification": True,
                "completed": False,
                "slots": slots,
                "profile_updates": None,
            }

        # 收口:合并画像,成功后才清会话上下文(合并失败可继续对话)
        result = await self.profile_service.merge_dialog_fields(user_id, slots)
        await self._delete_ctx(ctx["session_id"])
        return {
            "session_id": ctx["session_id"],
            "reply": "画像已更新,本次更新要素如下,可在画像报告中确认或修正。",
            "needs_clarification": False,
            "completed": True,
            "slots": slots,
            "profile_updates": result["profile_updates"],
        }

    async def _extract(self, history: list[dict]) -> DialogExtraction:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
        try:
            data = await self.llm.chat_json(messages, temperature=0.1, purpose="profile-dialog")
            return DialogExtraction(**data)
        except (ValidationError, TypeError) as exc:
            logger.warning("对话画像抽取结果结构无效,按空抽取处理:%s", exc)
            return DialogExtraction()

    def _merge_slots(self, current: dict, extraction: DialogExtraction) -> dict:
        """非空槽位覆盖旧值(用户最新表述优先,US-05 更新语义)。"""
        merged = dict(current)
        for name in ("risk_tolerance", "return_expectation", "investment_horizon", "holding_habit"):
            slot = getattr(extraction, name)
            if slot is not None:
                merged[name] = slot.model_dump()
        return merged

    def _must_slots_complete(self, slots: dict) -> bool:
        """必备三要素齐备即收口(holding_habit 为机会抽取,不强制)。"""
        return all(name in slots for name in _CLARIFY_ORDER)

    def _clarification_questions(self, slots: dict) -> list[str]:
        return [_CLARIFY_TEMPLATES[name] for name in _CLARIFY_ORDER if name not in slots][
            :MAX_QUESTIONS_PER_ROUND
        ]

    async def _load_ctx(self, session_id: str | None) -> dict:
        if session_id:
            ctx = await self.cache.get_json(session_ctx_key(session_id))
            if ctx is not None:
                return ctx
        return {"session_id": secrets.token_hex(8), "messages": [], "slots": {}, "rounds": 0}

    async def _save_ctx(self, ctx: dict) -> None:
        await self.cache.set_json(session_ctx_key(ctx["session_id"]), ctx, ttl=SESSION_CTX_TTL_SECONDS)

    async def _delete_ctx(self, session_id: str) -> None:
        await self.cache.delete(session_ctx_key(session_id))
