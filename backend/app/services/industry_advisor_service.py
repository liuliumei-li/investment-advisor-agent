"""行业/概念板块分析服务(US-07):板块数据 → LLM 分析 → 双 gate → 四要素落库。

复用 US-06 的 gate 与画像匹配机制;AC-1 三维度(景气度/资金流向/政策催化)由提示词强制覆盖,
AC-3 时效经 data_citations.data_timestamp 落地,AC-4 下行风险提示由提示词与合规 gate 双保险。
"""

import logging
import time
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationFailed
from app.datasource.base import SOURCE_TYPE_PROFILE
from app.datasource.market_data import MarketDataService
from app.llm.base import LLMClient
from app.models.advice import Advice, AgentRun, ComplianceAction, ComplianceAuditLog, ComplianceStatus, DataCitation
from app.models.chat import Scenario
from app.models.user_profile import RISK_LEVEL_LABELS
from app.repositories.advice_repo import AdviceRepository
from app.repositories.profile_repo import ProfileRepository
from app.services.compliance_service import audit_advice
from app.services.hallucination_service import annotate_dubious, validate_market_advice
from app.services.market_advisor_service import (
    KeyFactor,
    LogicStep,
    PositionSuggestion,
    ReturnExpectation,
    _flatten_numbers,
)
from app.services.profile_report import serialize_profile_compact, stock_cap_for_level

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是证券投顾系统的行业研究智能体,基于提供的板块行情、财经快讯与研报数据做行业/概念板块分析。\n"
    "输出要求(BR-ADV-01 四要素):\n"
    "1. industry_review:板块整体解读,必须覆盖三个维度——景气度(涨跌幅与研报观点)、"
    "资金流向(主力净流入)、政策催化(快讯/研报中的政策信息),并注明数据时效;\n"
    "2. key_factors:关键影响因素 2~4 条,每条 title/detail/source_refs(引用编号);\n"
    "3. logic_chain:逻辑链条 3~5 步,每步 content/source_refs(支持逐级展开);\n"
    "4. conclusion:核心结论一段话,含对用户指定板块的配置观点;\n"
    "5. risk_tips:风险提示(必须包含下行风险,如板块回调/政策不及预期/资金退潮);\n"
    "6. position_suggestion:与用户风险等级匹配的配置方向,action 取值 加配/标配/低配/回避,"
    "reason 说明匹配理由(BR-IMG-04 仓位上限矩阵已在数据清单中给出);\n"
    "7. return_expectation:板块中性收益预期区间(年化%,low≤high),不确定输出 null,禁止收益承诺。\n"
    "硬性约束:只允许使用提供数据中的数字与事实,严禁编造;引用必须用「来源N」编号;"
    "禁止保证收益/稳赚/无风险等承诺表述;输出 JSON 对象。"
)


class IndustryAdviceDraft(BaseModel):
    """LLM 板块分析草稿(经两道 gate 校验后才允许落库输出)。"""

    industry_review: str
    key_factors: list[KeyFactor]
    logic_chain: list[LogicStep]
    conclusion: str
    risk_tips: str | list[str]
    position_suggestion: PositionSuggestion
    return_expectation: ReturnExpectation | None = None

    @field_validator("risk_tips", mode="before")
    @classmethod
    def _join_risk_tips(cls, value):
        if isinstance(value, list):
            return "\n".join(str(item) for item in value if str(item).strip())
        return value


class IndustryAdvisorService:
    """板块分析编排:取数(含板块行情)→ 生成 → 校验 → 合规 → 落库。"""

    def __init__(
        self,
        llm: LLMClient,
        market_data: MarketDataService,
        profile_repo: ProfileRepository,
        advice_repo: AdviceRepository,
        session: AsyncSession,
    ):
        self.llm = llm
        self.market_data = market_data
        self.profile_repo = profile_repo
        self.advice_repo = advice_repo
        self.session = session

    async def analyze(self, user_id: int, session_id: int, content: str, progress=None) -> dict:
        started = time.perf_counter()
        trace: list[dict] = []

        async def _step(step: dict) -> None:
            trace.append(step)
            if progress is not None:
                await progress(step)

        profile = await self.profile_repo.get_by_user_id(user_id)
        if profile is None:
            raise ValidationFailed("请先完成画像建立(问卷/对话/持仓导入)后再发起板块分析")
        profile_view = serialize_profile_compact(profile)
        stock_cap = stock_cap_for_level(profile.risk_level)

        snapshot = await self.market_data.fetch_market_snapshot(with_boards=True)
        await _step({"step": "取数", "detail": f"板块 {len(snapshot['boards'])} 条,快讯 {len(snapshot['news'])} 条,"
                      f"研报 {len(snapshot['research'])} 条,降级源 {len(snapshot['degraded'])} 个"})

        provided = self._provided_points(snapshot)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_context(provided, profile_view, stock_cap, content)},
        ]
        data = await self.llm.chat_json(messages, temperature=0.3, purpose="industry-advice")
        try:
            draft = IndustryAdviceDraft(**data)
        except (ValueError, TypeError) as exc:
            logger.warning("板块分析草稿结构无效:%s", exc)
            raise ValidationFailed("板块分析生成结果结构异常,请重试") from exc
        await _step(
            {"step": "生成", "detail": f"影响因素 {len(draft.key_factors)} 条,逻辑链 {len(draft.logic_chain)} 步"}
        )

        fields = draft.model_dump()
        validation = validate_market_advice(fields, provided)
        await _step({"step": "校验", "detail": f"幻觉检测 {len(validation['issues'])} 条问题"})
        if validation["rejected"]:
            raise ValidationFailed("板块分析的关键引用无法通过数据校验,请稍后重试")
        if validation["issues"]:
            unverified = [
                issue["detail"].split(":")[1].strip()
                for issue in validation["issues"]
                if issue["type"] == "unverified_number"
            ]
            fields = annotate_dubious(fields, _flatten_numbers(unverified))

        audit = audit_advice(fields)
        fields = audit["fields"]
        await _step({"step": "合规", "detail": f"命中 {len(audit['matched_rules'])} 条规则,动作 {audit['action']}"})

        position = fields["position_suggestion"]
        position["risk_level"] = profile.risk_level.value
        position["risk_level_name"] = RISK_LEVEL_LABELS[profile.risk_level]
        position["stock_cap"] = stock_cap

        advice = await self.advice_repo.add_advice(
            Advice(
                session_id=session_id,
                user_id=user_id,
                scenario=Scenario.INDUSTRY,
                conclusion=fields["conclusion"],
                logic_chain=fields["logic_chain"],
                risk_tips=fields["risk_tips"],
                position_suggestion=position,
                return_expectation=fields.get("return_expectation"),
                compliance_status=ComplianceStatus(audit["compliance_status"]),
            )
        )
        citations = [
            DataCitation(
                advice_id=advice.id,
                source_name=point["source_name"],
                source_type=point["source_type"],
                data_point=point["data_point"],
                source_url=point["source_url"],
                data_timestamp=point["data_timestamp"],
                verified=point["ref"] in validation["verified_refs"],
            )
            for point in provided
        ]
        citations.append(
            DataCitation(
                advice_id=advice.id,
                source_name="用户画像",
                source_type=SOURCE_TYPE_PROFILE,
                data_point=f"风险等级 {RISK_LEVEL_LABELS[profile.risk_level]}({profile.risk_level.value}),"
                f"股票类仓位上限 {stock_cap}",
                source_url="",
                data_timestamp=datetime.now(timezone.utc).isoformat(),
                verified=True,
            )
        )
        await self.advice_repo.add_citations(citations)
        duration_ms = int((time.perf_counter() - started) * 1000)
        await self.advice_repo.add_agent_run(
            AgentRun(advice_id=advice.id, coordinator_trace=trace, duration_ms=duration_ms)
        )
        await self.advice_repo.add_compliance_log(
            ComplianceAuditLog(
                advice_id=advice.id,
                passed=audit["passed"],
                matched_rules=audit["matched_rules"],
                action=ComplianceAction(audit["action"]),
            )
        )
        await self.session.commit()
        return {
            "advice_id": advice.id,
            "conclusion": fields["conclusion"],
            "risk_tips": fields["risk_tips"],
            "compliance_status": audit["compliance_status"],
            "degraded": snapshot["degraded"],
            "citations_count": len(citations),
            "duration_ms": duration_ms,
            "validation_issue_count": len(validation["issues"]),
        }

    @staticmethod
    def _provided_points(snapshot: dict) -> list[dict]:
        points: list[dict] = []
        ref_index = 1
        for group in (snapshot["boards"], snapshot["news"], snapshot["research"]):
            for point in group:
                points.append(
                    {
                        "ref": f"来源{ref_index}",
                        "source_name": point.source_name,
                        "source_type": point.source_type,
                        "data_point": point.data_point,
                        "source_url": point.source_url,
                        "data_timestamp": point.data_timestamp,
                    }
                )
                ref_index += 1
        return points

    @staticmethod
    def _build_context(provided: list[dict], profile_view: dict, stock_cap: str, content: str) -> str:
        lines = ["提供数据(引用请用「来源N」编号,含时间戳须注明时效):"]
        for point in provided:
            lines.append(
                f"{point['ref']} [{point['source_name']}] {point['data_point']}(时间 {point['data_timestamp']})"
            )
        lines.append(
            "用户画像:"
            f"风险等级 {profile_view['risk_level_name']}({profile_view['risk_level']}),"
            f"收益预期 {profile_view['return_expectation_low']}%~{profile_view['return_expectation_high']}%,"
            f"投资期限 {profile_view['investment_horizon']};"
            f"BR-IMG-04 股票类仓位上限:{stock_cap}。"
        )
        lines.append(f"用户问题:{content.strip()}")
        return "\n".join(lines)
