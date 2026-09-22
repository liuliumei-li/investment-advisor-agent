"""个股分析服务(US-08):提取标的 → 行情/财务取数 → 基本面+技术面分析 → 双 gate → 落库。

- 两段式 LLM:先提取股票代码(6 位,按 6→sh / 0,3→sz 规则加市场前缀),再生成分析;
- AC-1 覆盖基本面(财务/估值)与技术面(走势/资金/量价);AC-2 财务与估值仅来自 F10 与行情源,
  引用编号强制、数字核验(BR-DAT-05);AC-3 风险提示强制追加「基于公开数据,不含未公开信息」局限标注;
- AC-4 仓位建议与画像匹配(复用 BR-IMG-04 矩阵)。
"""

import logging
import time
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationFailed
from app.datasource.base import SOURCE_TYPE_PROFILE
from app.datasource.eastmoney import EastmoneyFinanceSource
from app.datasource.tencent import TencentStockQuoteSource
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
    parse_return_expectation,
)
from app.services.profile_report import serialize_profile_compact, stock_cap_for_level

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = (
    "你是证券投顾系统的股票识别模块。从用户消息中提取其想分析的 A 股标的,输出 JSON。\n"
    '规则:1. 提取六位股票代码,输出 {"code": "600519"};2. 消息中未出现任何股票名称/代码时输出 {"code": null};'
    "3. 只输出 JSON,不要其他文字。"
)

SYSTEM_PROMPT = (
    "你是证券投顾系统的个股研究智能体,基于提供的行情与财务数据做个股分析。\n"
    "输出要求(BR-ADV-01 四要素):\n"
    "1. fundamental_review:基本面解读(财务指标质量、成长性、估值水平 PE/PB);\n"
    "2. technical_review:技术面/行情面解读(走势位置、量价、换手、52周位置);\n"
    "3. key_factors:关键因素 2~4 条,每条 title/detail/source_refs;\n"
    "4. logic_chain:逻辑链条 3~5 步,每步 content/source_refs;\n"
    "5. conclusion:核心结论一段话;\n"
    "6. risk_tips:风险提示(个股特有风险:估值过高、业绩不及预期、流动性、行业政策等);\n"
    "7. position_suggestion:组合视角参考建议,action 取值 加仓/持有/减仓/观望,reason 说明与用户画像的匹配理由;\n"
    "8. return_expectation:中性收益预期区间(年化%),不确定输出 null,禁止收益承诺。\n"
    "硬性约束:只允许使用提供数据中的数字与事实,严禁编造财务数据或估值;引用必须用「来源N」编号;"
    "禁止保证收益/稳赚/无风险等承诺表述;输出 JSON 对象。"
)

# AC-3:分析局限标注(强制追加)
LIMITATION_NOTE = "本分析基于公开数据,不含未公开信息;财务数据来自定期报告,存在披露滞后,请以最新公告为准。"


class StockAdviceDraft(BaseModel):
    fundamental_review: str
    technical_review: str
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

    @field_validator("return_expectation", mode="before")
    @classmethod
    def _parse_return(cls, value):
        return parse_return_expectation(value)

    @field_validator("fundamental_review", "technical_review", "conclusion", mode="before")
    @classmethod
    def _unwrap_dict(cls, value):
        if isinstance(value, dict):
            for key in ("summary", "content", "text"):
                if isinstance(value.get(key), str) and value[key].strip():
                    return value[key]
            for item in value.values():
                if isinstance(item, str) and item.strip():
                    return item
        return value


class StockAdvisorService:
    """个股分析编排:提取代码 → 取数(行情+F10)→ 分析 → 双 gate → 落库。"""

    def __init__(
        self, llm: LLMClient, profile_repo: ProfileRepository, advice_repo: AdviceRepository, session: AsyncSession
    ):
        self.llm = llm
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
            raise ValidationFailed("请先完成画像建立(问卷/对话/持仓导入)后再发起个股分析")
        profile_view = serialize_profile_compact(profile)
        stock_cap = stock_cap_for_level(profile.risk_level)

        code = await self._extract_code(content)
        if code is None:
            raise ValidationFailed("未能识别您想分析的股票,请提供股票名称或六位代码(如:贵州茅台 / 600519)")
        market_code = self._market_code(code)
        await _step({"step": "识别", "detail": f"标的 {market_code}"})

        provided, degraded = await self._fetch_data(market_code)
        await _step({"step": "取数", "detail": f"行情/财务 {len(provided)} 条,降级源 {len(degraded)} 个"})

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._build_context(provided, profile_view, stock_cap, content)},
        ]
        data = await self.llm.chat_json(messages, temperature=0.3, purpose="stock-advice")
        try:
            draft = StockAdviceDraft(**data)
        except (ValueError, TypeError) as exc:
            logger.warning("个股分析草稿结构无效:%s", exc)
            raise ValidationFailed("个股分析生成结果结构异常,请重试") from exc
        await _step(
            {"step": "生成", "detail": f"影响因素 {len(draft.key_factors)} 条,逻辑链 {len(draft.logic_chain)} 步"}
        )

        fields = draft.model_dump()
        validation = validate_market_advice(fields, provided)
        await _step({"step": "校验", "detail": f"幻觉检测 {len(validation['issues'])} 条问题"})
        if validation["rejected"]:
            raise ValidationFailed("个股分析的关键引用无法通过数据校验,请稍后重试")
        if validation["issues"]:
            unverified = [
                issue["detail"].split(":")[1].strip()
                for issue in validation["issues"]
                if issue["type"] == "unverified_number"
            ]
            fields = annotate_dubious(fields, _flatten_numbers(unverified))

        fields["risk_tips"] = f"{fields['risk_tips'].rstrip()}\n{LIMITATION_NOTE}"  # AC-3 局限标注
        audit = audit_advice(fields)
        fields = audit["fields"]
        await _step({"step": "合规", "detail": f"命中 {len(audit['matched_rules'])} 条规则,动作 {audit['action']}"})

        position = fields["position_suggestion"]
        position["risk_level"] = profile.risk_level.value
        position["risk_level_name"] = RISK_LEVEL_LABELS[profile.risk_level]
        position["stock_cap"] = stock_cap

        advice = await self.advice_repo.add_advice(
            Advice(
                session_id=session_id, user_id=user_id, scenario=Scenario.STOCK,
                conclusion=fields["conclusion"], logic_chain=fields["logic_chain"],
                risk_tips=fields["risk_tips"], position_suggestion=position,
                return_expectation=fields.get("return_expectation"),
                compliance_status=ComplianceStatus(audit["compliance_status"]),
            )
        )
        citations = [
            DataCitation(
                advice_id=advice.id, source_name=point["source_name"], source_type=point["source_type"],
                data_point=point["data_point"], source_url=point["source_url"],
                data_timestamp=point["data_timestamp"], verified=point["ref"] in validation["verified_refs"],
            )
            for point in provided
        ]
        citations.append(
            DataCitation(
                advice_id=advice.id, source_name="用户画像", source_type=SOURCE_TYPE_PROFILE,
                data_point=f"风险等级 {RISK_LEVEL_LABELS[profile.risk_level]}({profile.risk_level.value}),"
                f"股票类仓位上限 {stock_cap}",
                source_url="", data_timestamp=datetime.now(timezone.utc).isoformat(), verified=True,
            )
        )
        await self.advice_repo.add_citations(citations)
        duration_ms = int((time.perf_counter() - started) * 1000)
        await self.advice_repo.add_agent_run(
            AgentRun(advice_id=advice.id, coordinator_trace=trace, duration_ms=duration_ms)
        )
        await self.advice_repo.add_compliance_log(
            ComplianceAuditLog(
                advice_id=advice.id, passed=audit["passed"], matched_rules=audit["matched_rules"],
                action=ComplianceAction(audit["action"]),
            )
        )
        await self.session.commit()
        return {
            "advice_id": advice.id, "conclusion": fields["conclusion"], "risk_tips": fields["risk_tips"],
            "compliance_status": audit["compliance_status"], "degraded": degraded,
            "citations_count": len(citations), "duration_ms": duration_ms,
            "validation_issue_count": len(validation["issues"]),
        }

    async def _extract_code(self, content: str) -> str | None:
        messages = [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": content.strip()}]
        data = await self.llm.chat_json(messages, temperature=0.1, purpose="stock-extract")
        code = str(data.get("code") or "").strip() if isinstance(data, dict) else ""
        return code if code.isdigit() and len(code) == 6 else None

    @staticmethod
    def _market_code(code: str) -> str:
        # 6 开头沪市,0/3 开头深市(创业板 3)
        return f"sh{code}" if code.startswith("6") else f"sz{code}"

    async def _fetch_data(self, market_code: str) -> tuple[list[dict], list[dict]]:
        secucode = f"{market_code[2:]}.{'SH' if market_code.startswith('sh') else 'SZ'}"
        provided: list[dict] = []
        degraded: list[dict] = []
        ref_index = 1
        for source in (TencentStockQuoteSource(market_code), EastmoneyFinanceSource(secucode)):
            try:
                points = await source.fetch()
                for point in points:
                    provided.append(
                        {
                            "ref": f"来源{ref_index}", "source_name": point.source_name,
                            "source_type": point.source_type, "data_point": point.data_point,
                            "source_url": point.source_url, "data_timestamp": point.data_timestamp,
                        }
                    )
                    ref_index += 1
            except Exception as exc:  # noqa: BLE001 单源故障降级标注
                degraded.append({"source": source.name, "reason": str(exc)})
        if not provided:
            raise ValidationFailed("个股行情与财务数据源均不可用,请稍后重试")
        return provided, degraded

    @staticmethod
    def _build_context(provided: list[dict], profile_view: dict, stock_cap: str, content: str) -> str:
        lines = ["提供数据(引用请用「来源N」编号,严禁编造):"]
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
