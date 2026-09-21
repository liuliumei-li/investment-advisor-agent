"""大盘研判服务(US-06):数据 → LLM 研判 → 两道 gate → 画像匹配 → 四要素落库。

设计要点(对齐 docs/architecture.md §1.4 决策与 §3.2 分层):
- 数据只经 MarketDataService(Datasource 适配器)获取,LLM 输入上下文带引用编号,禁止编造数字;
- 输出必经 validate(幻觉检测)与 compliance(合规审核)两道 gate,不可绕过;
- 研判结论、引用、运行记录、合规日志同事务落库(BR-DAT-04、BR-CMP-04);
- 仓位建议与画像风险等级匹配(BR-ADV-04、BR-IMG-04):prompt 注入矩阵,响应附确定性仓位上限。
"""

import logging
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field
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
from app.services.profile_report import serialize_profile_compact, stock_cap_for_level

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是证券投顾系统的宏观研究智能体,基于提供的实时行情、财经快讯与研报数据对 A 股大盘做研判。\n"
    "输出要求(BR-ADV-01 四要素):\n"
    "1. market_review:市场整体走势解读(今日指数表现、量价特征、板块风格);\n"
    "2. key_factors:关键影响因素 2~4 条,每条给出 title 与 detail,"
    "并标注依据的引用编号 source_refs(如[\"来源1\",\"来源3\"]);\n"
    "3. logic_chain:逻辑链条 3~5 步,从数据证据逐步推到结论,每步标注 content 与 source_refs(支持逐级展开);\n"
    "4. conclusion:核心结论一段话;\n"
    "5. risk_tips:风险提示(必须包含,不得为空);\n"
    "6. position_suggestion:结合用户画像的操作提示,action 取值 加仓/持有/减仓/观望,reason 说明与画像的匹配理由,\n"
    "   仓位方向必须与用户风险等级匹配(BR-IMG-04 股票类仓位上限矩阵,已在数据清单中给出);\n"
    "7. return_expectation:当前市场环境下的中性收益预期区间(年化百分比,low≤high),不确定时输出 null,禁止收益承诺。\n"
    "硬性约束(违规则输出无效):\n"
    "- 只允许使用「提供数据」中的数字与事实,严禁编造数据点、指数点位或百分比;\n"
    "- 引用必须用「来源N」编号注明,数字必须与所引来源一致;\n"
    "- 禁止出现保证收益、稳赚、无风险等承诺性表述;\n"
    "- 输出 JSON 对象,不要输出其他文字。"
)


class KeyFactor(BaseModel):
    title: str
    detail: str
    source_refs: list[str] = Field(default_factory=list)


class LogicStep(BaseModel):
    step: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class PositionSuggestion(BaseModel):
    action: str  # 加仓/持有/减仓/观望
    reason: str


class ReturnExpectation(BaseModel):
    low: float
    high: float


class MarketAdviceDraft(BaseModel):
    """LLM 研判草稿(经两道 gate 校验后才允许落库输出)。"""

    market_review: str
    key_factors: list[KeyFactor]
    logic_chain: list[LogicStep]
    conclusion: str
    risk_tips: str
    position_suggestion: PositionSuggestion
    return_expectation: ReturnExpectation | None = None


class MarketAdvisorService:
    """大盘研判编排:取数 → 生成 → 校验 → 合规 → 落库(不碰外部协议与 SQL,经适配器与仓储)。"""

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

    async def analyze(self, user_id: int, session_id: int, content: str) -> dict:
        started = time.perf_counter()
        trace: list[dict] = []

        profile = await self.profile_repo.get_by_user_id(user_id)
        if profile is None:
            # UC-02 前置条件:用户画像已建立(AC-4 个性化匹配的必要输入)
            raise ValidationFailed("请先完成画像建立(问卷/对话/持仓导入)后再发起大盘研判")
        profile_view = serialize_profile_compact(profile)
        stock_cap = stock_cap_for_level(profile.risk_level)

        snapshot = await self.market_data.fetch_market_snapshot()
        trace.append({"step": "取数", "detail": f"行情 {len(snapshot['quotes'])} 条,快讯 {len(snapshot['news'])} 条,"
                      f"研报 {len(snapshot['research'])} 条,降级源 {len(snapshot['degraded'])} 个"})

        provided = self._provided_points(snapshot)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_context(provided, profile_view, stock_cap, content)},
        ]
        data = await self.llm.chat_json(messages, temperature=0.3, purpose="market-advice")
        try:
            draft = MarketAdviceDraft(**data)
        except (ValueError, TypeError) as exc:
            logger.warning("研判草稿结构无效:%s", exc)
            raise ValidationFailed("研判生成结果结构异常,请重试") from exc
        trace.append(
            {"step": "生成", "detail": f"影响因素 {len(draft.key_factors)} 条,逻辑链 {len(draft.logic_chain)} 步"}
        )

        fields = draft.model_dump()
        validation = validate_market_advice(fields, provided)
        trace.append({"step": "校验", "detail": f"幻觉检测 {len(validation['issues'])} 条问题"})
        if validation["rejected"]:
            raise ValidationFailed("研判结果的关键引用无法通过数据校验,请稍后重试")
        if validation["issues"]:
            unverified = [
                issue["detail"].split(":")[1].strip()
                for issue in validation["issues"]
                if issue["type"] == "unverified_number"
            ]
            fields = annotate_dubious(fields, _flatten_numbers(unverified))

        audit = audit_advice(fields)
        fields = audit["fields"]
        trace.append({"step": "合规", "detail": f"命中 {len(audit['matched_rules'])} 条规则,动作 {audit['action']}"})

        position = fields["position_suggestion"]
        position["risk_level"] = profile.risk_level.value
        position["risk_level_name"] = RISK_LEVEL_LABELS[profile.risk_level]
        position["stock_cap"] = stock_cap  # BR-IMG-04 匹配依据(AC-4 可解释差异)

        advice = await self.advice_repo.add_advice(
            Advice(
                session_id=session_id,
                user_id=user_id,
                scenario=Scenario.MARKET,
                conclusion=fields["conclusion"],
                logic_chain=fields["logic_chain"],
                risk_tips=fields["risk_tips"],
                position_suggestion=position,
                return_expectation=fields.get("return_expectation"),
                compliance_status=ComplianceStatus(audit["compliance_status"]),
            )
        )
        # 引用落库:提供数据逐条入 data_citations,被 LLM 引用且通过校验的 verified=True(BR-DAT-05)
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
        """快照数据 → 带引用编号的提供数据清单(LLM 上下文与幻觉校验共用)。"""
        points: list[dict] = []
        ref_index = 1
        for group in (snapshot["quotes"], snapshot["news"], snapshot["research"]):
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
        """LLM 输入上下文:提供数据清单(带编号)+ 画像要素 + 用户问题。"""
        lines = ["提供数据(引用请用「来源N」编号):"]
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


def _flatten_numbers(detail_values: list[str]) -> list[str]:
    """'来源X 与 a,b,c' 形式的 issue detail → 数字列表(标注存疑用)。"""
    numbers: list[str] = []
    for value in detail_values:
        numbers.extend(part.strip() for part in value.split(",") if part.strip())
    return numbers
