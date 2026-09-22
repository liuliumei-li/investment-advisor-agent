"""个股分析服务单测(US-08):两段式提取/取数/分析、双 gate、局限标注、画像匹配(AC-1~AC-4)。"""

import pytest

from app.core.exceptions import ValidationFailed
from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.datasource.base import DataPoint
from app.datasource.eastmoney import EastmoneyFinanceSource
from app.datasource.tencent import TencentStockQuoteSource
from app.models.chat import ChatSession, Scenario
from app.repositories.advice_repo import AdviceRepository
from app.repositories.chat_repo import ChatRepository
from app.repositories.profile_repo import ProfileRepository
from app.repositories.questionnaire_repo import QuestionnaireRepository
from app.services.profile_service import ProfileService
from app.services.stock_advisor_service import LIMITATION_NOTE, StockAdvisorService
from tests.helpers import FakeLLM, full_answers

QUESTIONS = QUESTIONNAIRE_V1["questions"]

DRAFT = {
    "fundamental_review": "基本EPS 35.57 元,加权ROE 25.00%,毛利率 91.00%,基本面稳健。",
    "technical_review": "现价 1253.80,涨跌幅 0.10%,换手率 0.20%,动态PE 19.25,PB 6.24。",
    "key_factors": [
        {"title": "业绩", "detail": "基本EPS 35.57 元。", "source_refs": ["来源2"]},
        {"title": "估值", "detail": "动态PE 19.25,PB 6.24。", "source_refs": ["来源1"]},
    ],
    "logic_chain": [{"step": "1", "content": "现价 1253.80,涨 0.10%。", "source_refs": ["来源1"]}],
    "conclusion": "贵州茅台现价 1253.80 元,动态PE 19.25,基本面稳健,可继续跟踪。",
    "risk_tips": "注意估值回落与业绩不及预期风险。",
    "position_suggestion": {"action": "持有", "reason": "当前风险等级适配"},
    "return_expectation": {"low": 5, "high": 12},
}


async def setup(db_session, cache, seeded_questionnaire, user_id=1):
    profile_service = ProfileService(
        QuestionnaireRepository(db_session), ProfileRepository(db_session), db_session, cache
    )
    await profile_service.submit_questionnaire(user_id, seeded_questionnaire, full_answers(QUESTIONS))
    session = await ChatRepository(db_session).create_session(ChatSession(user_id=user_id, scenario=Scenario.STOCK))
    await db_session.commit()
    return session.id


def make_advisor(db_session, llm):
    return StockAdvisorService(
        llm=llm, profile_repo=ProfileRepository(db_session),
        advice_repo=AdviceRepository(db_session), session=db_session,
    )


class TestAnalyze:
    async def test_full_flow_two_stage_persists(self, db_session, cache, seeded_questionnaire, monkeypatch):
        session_id = await setup(db_session, cache, seeded_questionnaire)
        llm = FakeLLM([{"code": "600519"}, DRAFT])
        advisor = make_advisor(db_session, llm)

        async def fetch_quote(self):
            return [DataPoint(source_name="腾讯个股行情", source_type="quote",
                              data_point="贵州茅台(600519):现价 1253.80,涨跌幅 0.10%,动态PE 19.25,PB 6.24,换手率 0.20%",
                              source_url="https://gu.qq.com/", data_timestamp="2026-09-22T10:00:00+00:00")]

        async def fetch_finance(self):
            return [
                DataPoint(
                    source_name="东方财富财务数据",
                    source_type="research",
                    data_point="贵州茅台(600519) 2026中报财务指标:基本EPS 35.57 元,加权ROE 25.00%,销售毛利率 91.00%",
                    source_url="https://emweb.securities.eastmoney.com/",
                    data_timestamp="2026-06-30",
                )
            ]

        monkeypatch.setattr(TencentStockQuoteSource, "fetch", fetch_quote)
        monkeypatch.setattr(EastmoneyFinanceSource, "fetch", fetch_finance)

        result = await advisor.analyze(1, session_id, "分析一下贵州茅台")

        assert result["compliance_status"] == "passed"
        assert len(llm.calls) == 2  # 两段式:提取代码 → 生成分析
        advice = await AdviceRepository(db_session).get_advice(result["advice_id"])
        assert advice.scenario is Scenario.STOCK
        assert advice.position_suggestion["stock_cap"] == "60%~80%"
        assert LIMITATION_NOTE in advice.risk_tips  # AC-3 局限标注
        assert "不构成投资建议" in advice.risk_tips
        assert result["validation_issue_count"] == 0  # 全部数字可核验(BR-DAT-05)
        citations = await AdviceRepository(db_session).list_citations(result["advice_id"])
        assert [c.source_name for c in citations] == ["腾讯个股行情", "东方财富财务数据", "用户画像"]
        assert all(citation.verified for citation in citations[:2])

    async def test_no_stock_identified_rejected(self, db_session, cache, seeded_questionnaire):
        session_id = await setup(db_session, cache, seeded_questionnaire)
        advisor = make_advisor(db_session, FakeLLM([{"code": None}]))
        with pytest.raises(ValidationFailed, match="未能识别"):
            await advisor.analyze(1, session_id, "今天天气怎么样")

    async def test_without_profile_rejected(self, db_session):
        session = await ChatRepository(db_session).create_session(ChatSession(user_id=9, scenario=Scenario.STOCK))
        await db_session.commit()
        with pytest.raises(ValidationFailed, match="请先完成画像建立"):
            await make_advisor(db_session, FakeLLM([])).analyze(9, session.id, "分析贵州茅台")

    def test_market_code_mapping(self):
        assert StockAdvisorService._market_code("600519") == "sh600519"
        assert StockAdvisorService._market_code("000858") == "sz000858"
        assert StockAdvisorService._market_code("300750") == "sz300750"
