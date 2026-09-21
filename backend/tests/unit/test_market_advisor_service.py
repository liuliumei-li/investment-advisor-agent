"""大盘研判服务单测(US-06):全链路生成 → 两道 gate → 四要素落库 → 画像匹配(AC-1~AC-4)。"""

import pytest

from app.core.exceptions import ValidationFailed
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.datasource.base import DataPoint, DataSource
from app.datasource.market_data import MarketDataService
from app.models.advice import Advice, ComplianceStatus
from app.models.chat import ChatSession, Scenario
from app.repositories.advice_repo import AdviceRepository
from app.repositories.chat_repo import ChatRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.market_advisor_service import MarketAdvisorService
from app.services.profile_service import ProfileService
from tests.helpers import FakeLLM, full_answers

QUESTIONS = QUESTIONNAIRE_V1["questions"]

QUOTE_POINT = DataPoint(
    source_name="新浪财经行情",
    source_type="quote",
    data_point="上证指数(000001) 3949.91,涨跌 0.97%",
    source_url="https://finance.sina.com.cn/realstock/company/sh000001/nc.shtml",
    data_timestamp="2026-09-21T10:00:00+00:00",
)
NEWS_POINT = DataPoint(
    source_name="新浪财经快讯",
    source_type="news",
    data_point="央行开展逆回购操作",
    source_url="https://finance.sina.com.cn/7x24/",
    data_timestamp="2026-09-21T10:00:00+00:00",
)

DRAFT = {
    "market_review": "今日上证指数 3949.91 点,上涨 0.97%,市场情绪回暖。",
    "key_factors": [{"title": "流动性", "detail": "央行开展逆回购操作,流动性宽松。", "source_refs": ["来源2"]}],
    "logic_chain": [
        {"step": "1", "content": "指数上涨 0.97%。", "source_refs": ["来源1"]},
        {"step": "2", "content": "政策面偏暖,利好风险偏好。", "source_refs": ["来源2"]},
    ],
    "conclusion": "上证指数报 3949.91 点,涨 0.97%,短期维持震荡上行判断。",
    "risk_tips": "注意回调风险。",
    "position_suggestion": {"action": "持有", "reason": "当前风险等级可承受一定波动,维持现有仓位"},
    "return_expectation": {"low": 5, "high": 10},
}


class FakeSource(DataSource):
    def __init__(self, name, points):
        self.name = name
        self.points = points

    async def fetch(self):
        return self.points


def make_market_data(cache):
    return MarketDataService(
        cache,
        quote_source=FakeSource("新浪财经行情", [QUOTE_POINT]),
        news_source=FakeSource("新浪财经快讯", [NEWS_POINT]),
        research_source=FakeSource("东方财富研报", []),
    )


def make_advisor(db_session, cache, llm):
    return MarketAdvisorService(
        llm=llm,
        market_data=make_market_data(cache),
        profile_repo=ProfileRepository(db_session),
        advice_repo=AdviceRepository(db_session),
        session=db_session,
    )


async def setup_profile_and_session(db_session, cache, seeded_questionnaire, user_id=1) -> tuple[int, ProfileService]:
    """问卷建立画像(全 c → C4)并创建市场咨询会话,返回 (session_id, profile_service)。"""
    profile_service = ProfileService(
        QuestionnaireRepository(db_session), ProfileRepository(db_session), db_session, cache
    )
    await profile_service.submit_questionnaire(user_id, seeded_questionnaire, full_answers(QUESTIONS))
    session = await ChatRepository(db_session).create_session(
        ChatSession(user_id=user_id, scenario=Scenario.MARKET)
    )
    await db_session.commit()
    return session.id, profile_service


class TestAnalyze:
    async def test_full_flow_persists_four_elements(self, db_session, cache, seeded_questionnaire):
        session_id, _ = await setup_profile_and_session(db_session, cache, seeded_questionnaire)
        advisor = make_advisor(db_session, cache, FakeLLM([DRAFT]))

        result = await advisor.analyze(1, session_id, "今天大盘怎么样?")

        assert result["advice_id"] >= 1
        assert result["conclusion"].startswith("上证指数报 3949.91")
        assert result["compliance_status"] == "passed"
        assert result["citations_count"] == 3  # 2 条提供数据 + 1 条画像引用
        assert result["duration_ms"] >= 0

        advice = await AdviceRepository(db_session).get_advice(result["advice_id"])
        assert advice.scenario is Scenario.MARKET
        assert len(advice.logic_chain) == 2  # BR-ADV-01 逻辑链条
        assert advice.risk_tips and "仅供参考,不构成投资建议" in advice.risk_tips  # BR-CMP-02
        # AC-4:仓位建议与画像 C4 匹配,确定性仓位上限 60%~80%(BR-IMG-04)
        assert advice.position_suggestion["action"] == "持有"
        assert advice.position_suggestion["stock_cap"] == "60%~80%"
        assert advice.position_suggestion["risk_level"] == "C4"
        assert advice.return_expectation == {"low": 5.0, "high": 10.0}
        assert advice.compliance_status is ComplianceStatus.PASSED

        citations = await AdviceRepository(db_session).list_citations(result["advice_id"])
        assert len(citations) == 3
        assert all(citation.verified for citation in citations[:2])  # 被引用且校验通过
        assert citations[2].source_type == "profile"
        assert "60%~80%" in citations[2].data_point

    async def test_without_profile_rejected(self, db_session, cache, seeded_questionnaire):
        session = await ChatRepository(db_session).create_session(ChatSession(user_id=9, scenario=Scenario.MARKET))
        await db_session.commit()
        advisor = make_advisor(db_session, cache, FakeLLM([]))
        with pytest.raises(ValidationFailed, match="请先完成画像建立"):
            await advisor.analyze(9, session.id, "今天大盘怎么样?")

    async def test_promise_word_rewritten_by_compliance(self, db_session, cache, seeded_questionnaire):
        session_id, _ = await setup_profile_and_session(db_session, cache, seeded_questionnaire)
        draft = {**DRAFT, "conclusion": "上证指数保证收益,稳赚!"}
        advisor = make_advisor(db_session, cache, FakeLLM([draft]))

        result = await advisor.analyze(1, session_id, "大盘?")

        assert "保证收益" not in result["conclusion"]
        assert "已按合规要求移除" in result["conclusion"]
        advice = await AdviceRepository(db_session).get_advice(result["advice_id"])
        assert advice.compliance_status is ComplianceStatus.PASSED  # 改写后合规

    async def test_unverified_number_annotated(self, db_session, cache, seeded_questionnaire):
        session_id, _ = await setup_profile_and_session(db_session, cache, seeded_questionnaire)
        draft = {**DRAFT, "conclusion": "上证指数报 4000.00 点,涨 0.97%,后市看涨。"}
        advisor = make_advisor(db_session, cache, FakeLLM([draft]))

        result = await advisor.analyze(1, session_id, "大盘?")

        assert "4000.00(数据存疑)" in result["conclusion"]  # BR-DAT-05 标注
        assert result["validation_issue_count"] >= 1

    async def test_invalid_refs_rejected(self, db_session, cache, seeded_questionnaire):
        session_id, _ = await setup_profile_and_session(db_session, cache, seeded_questionnaire)
        draft = {
            **DRAFT,
            "key_factors": [{"title": "x", "detail": "y", "source_refs": ["来源9"]}],
            "logic_chain": [{"step": "1", "content": "z", "source_refs": ["来源8"]}],
        }
        advisor = make_advisor(db_session, cache, FakeLLM([draft]))
        with pytest.raises(ValidationFailed, match="关键引用无法通过数据校验"):
            await advisor.analyze(1, session_id, "大盘?")
        # 拒答时不得落库
        advices = await db_session.execute(__import__("sqlalchemy").select(Advice))
        assert not advices.scalars().all()

    async def test_position_cap_differs_by_risk_level(self, db_session, cache, seeded_questionnaire):
        # C1 用户(全 a 选项:总分 20 → 保守型)
        profile_service = ProfileService(
            QuestionnaireRepository(db_session), ProfileRepository(db_session), db_session, cache
        )
        await profile_service.submit_questionnaire(1, seeded_questionnaire, full_answers(QUESTIONS, option_id="a"))
        session = await ChatRepository(db_session).create_session(ChatSession(user_id=1, scenario=Scenario.MARKET))
        await db_session.commit()
        advisor = make_advisor(db_session, cache, FakeLLM([DRAFT]))

        result = await advisor.analyze(1, session.id, "大盘?")
        advice = await AdviceRepository(db_session).get_advice(result["advice_id"])
        # BR-ADV-04:同一问题不同画像输出存在可解释差异(仓位上限随风险等级变化)
        assert advice.position_suggestion["risk_level"] == "C1"
        assert advice.position_suggestion["stock_cap"] == "≤20%"

    async def test_risk_tips_list_tolerated(self, db_session, cache, seeded_questionnaire):
        """真实 LLM 可能把 risk_tips 输出为数组 → Schema 容错归一为换行拼接。"""
        session_id, _ = await setup_profile_and_session(db_session, cache, seeded_questionnaire)
        draft = {**DRAFT, "risk_tips": ["注意回调风险。", "外部不确定性较大。"]}
        advisor = make_advisor(db_session, cache, FakeLLM([draft]))

        result = await advisor.analyze(1, session_id, "大盘?")

        assert "注意回调风险。" in result["risk_tips"]
        assert "外部不确定性较大。" in result["risk_tips"]
        assert result["compliance_status"] == "passed"

    async def test_prompt_contains_profile_and_numbered_sources(self, db_session, cache, seeded_questionnaire):
        session_id, _ = await setup_profile_and_session(db_session, cache, seeded_questionnaire)
        llm = FakeLLM([DRAFT])
        advisor = make_advisor(db_session, cache, llm)

        await advisor.analyze(1, session_id, "今天大盘怎么样?")

        user_message = llm.calls[0][1]["content"]
        assert "来源1 [新浪财经行情]" in user_message
        assert "来源2 [新浪财经快讯]" in user_message
        assert "风险等级 进取型(C4)" in user_message
        assert "60%~80%" in user_message  # BR-IMG-04 矩阵注入
        assert "今天大盘怎么样?" in user_message
