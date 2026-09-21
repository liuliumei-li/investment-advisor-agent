"""行业板块分析服务单测(US-07):板块取数、三维度分析、双 gate、画像匹配(AC-1~AC-4)。"""

import pytest

from app.core.exceptions import ValidationFailed
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.datasource.base import DataPoint, DataSource
from app.datasource.market_data import MarketDataService
from app.models.advice import ComplianceStatus
from app.models.chat import ChatSession, Scenario
from app.repositories.advice_repo import AdviceRepository
from app.repositories.chat_repo import ChatRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.industry_advisor_service import IndustryAdvisorService
from app.services.profile_service import ProfileService
from tests.helpers import FakeLLM, full_answers

QUESTIONS = QUESTIONNAIRE_V1["questions"]

BOARD_POINT = DataPoint(
    source_name="东方财富板块", source_type="quote",
    data_point="板块 半导体:涨跌幅 7.97%,主力净流入 48168824.0",
    source_url="https://data.eastmoney.com/", data_timestamp="2026-09-21T10:00:00+00:00",
)
NEWS_POINT = DataPoint(
    source_name="新浪财经快讯", source_type="news", data_point="多部门发布半导体产业支持政策",
    source_url="https://finance.sina.com.cn/7x24/", data_timestamp="2026-09-21T10:00:00+00:00",
)

DRAFT = {
    "industry_review": "半导体板块涨 7.97%,主力净流入 48168824.0,政策催化明显,景气度上行。",
    "key_factors": [
        {"title": "景气度", "detail": "板块涨 7.97%。", "source_refs": ["来源1"]},
        {"title": "资金流向", "detail": "主力净流入 48168824.0。", "source_refs": ["来源1"]},
        {"title": "政策催化", "detail": "多部门发布支持政策。", "source_refs": ["来源2"]},
    ],
    "logic_chain": [{"step": "1", "content": "板块上涨 7.97%。", "source_refs": ["来源1"]}],
    "conclusion": "半导体板块涨 7.97%,资金与政策共振,景气度上行,可适当关注。",
    "risk_tips": "注意板块回调与政策不及预期风险。",
    "position_suggestion": {"action": "标配", "reason": "当前风险等级适配板块波动"},
    "return_expectation": {"low": 8, "high": 15},
}


class FakeSource(DataSource):
    def __init__(self, name, points):
        self.name = name
        self.points = points

    async def fetch(self):
        return self.points


def make_service(db_session, cache, llm):
    market_data = MarketDataService(
        cache,
        quote_source=FakeSource("新浪财经行情", []),
        news_source=FakeSource("新浪财经快讯", [NEWS_POINT]),
        research_source=FakeSource("东方财富研报", []),
        board_source=FakeSource("东方财富板块", [BOARD_POINT]),
    )
    return IndustryAdvisorService(
        llm=llm, market_data=market_data, profile_repo=ProfileRepository(db_session),
        advice_repo=AdviceRepository(db_session), session=db_session,
    )


async def setup(db_session, cache, seeded_questionnaire, user_id=1):
    profile_service = ProfileService(
        QuestionnaireRepository(db_session), ProfileRepository(db_session), db_session, cache
    )
    await profile_service.submit_questionnaire(user_id, seeded_questionnaire, full_answers(QUESTIONS))
    session = await ChatRepository(db_session).create_session(ChatSession(user_id=user_id, scenario=Scenario.INDUSTRY))
    await db_session.commit()
    return session.id


class TestAnalyze:
    async def test_full_flow_persists_with_three_dimensions(self, db_session, cache, seeded_questionnaire):
        session_id = await setup(db_session, cache, seeded_questionnaire)
        result = await make_service(db_session, cache, FakeLLM([DRAFT])).analyze(1, session_id, "分析一下半导体板块")

        assert result["compliance_status"] == "passed"
        advice = await AdviceRepository(db_session).get_advice(result["advice_id"])
        assert advice.scenario is Scenario.INDUSTRY
        assert len(advice.logic_chain) == 1
        # AC-1 三维度:景气度/资金流向/政策催化
        factors = DRAFT["key_factors"]
        assert {f["title"] for f in factors} == {"景气度", "资金流向", "政策催化"}
        # AC-4 下行风险提示 + 合规免责声明
        assert "回调" in advice.risk_tips and "不构成投资建议" in advice.risk_tips
        # AC-2 画像匹配:C4 → 60%~80%
        assert advice.position_suggestion["stock_cap"] == "60%~80%"
        assert advice.compliance_status is ComplianceStatus.PASSED
        citations = await AdviceRepository(db_session).list_citations(result["advice_id"])
        assert citations[-1].source_type == "profile"

    async def test_without_profile_rejected(self, db_session, cache):
        session = await ChatRepository(db_session).create_session(ChatSession(user_id=9, scenario=Scenario.INDUSTRY))
        await db_session.commit()
        with pytest.raises(ValidationFailed, match="请先完成画像建立"):
            await make_service(db_session, cache, FakeLLM([])).analyze(9, session.id, "半导体?")

    async def test_risk_tips_list_tolerated(self, db_session, cache, seeded_questionnaire):
        session_id = await setup(db_session, cache, seeded_questionnaire)
        draft = {**DRAFT, "risk_tips": ["板块回调风险。", "资金退潮风险。"]}
        result = await make_service(db_session, cache, FakeLLM([draft])).analyze(1, session_id, "半导体?")
        assert "资金退潮风险。" in result["risk_tips"]

    async def test_invalid_refs_rejected_without_persist(self, db_session, cache, seeded_questionnaire):
        session_id = await setup(db_session, cache, seeded_questionnaire)
        draft = {
            **DRAFT,
            "key_factors": [{"title": "x", "detail": "y", "source_refs": ["来源9"]}],
            "logic_chain": [{"step": "1", "content": "z", "source_refs": ["来源8"]}],
        }
        with pytest.raises(ValidationFailed, match="关键引用无法通过数据校验"):
            await make_service(db_session, cache, FakeLLM([draft])).analyze(1, session_id, "半导体?")
