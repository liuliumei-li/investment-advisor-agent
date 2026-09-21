"""咨询会话接口集成测试(US-06):会话创建、SSE 流式研判、历史消息、建议详情与错误码。"""

from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.datasource import get_market_data_service
from app.datasource.base import DataPoint, DataSource
from app.datasource.market_data import MarketDataService
from app.llm import get_llm_client
from app.main import app
from tests.helpers import FakeLLM, full_answers, register_and_login

QUESTIONS = QUESTIONNAIRE_V1["questions"]

QUOTE_POINT = DataPoint(
    source_name="新浪财经行情", source_type="quote",
    data_point="上证指数(000001) 3949.91,涨跌 0.97%", source_url="https://finance.sina.com.cn/",
    data_timestamp="2026-09-21T10:00:00+00:00",
)
NEWS_POINT = DataPoint(
    source_name="新浪财经快讯", source_type="news",
    data_point="央行开展逆回购操作", source_url="https://finance.sina.com.cn/7x24/",
    data_timestamp="2026-09-21T10:00:00+00:00",
)

DRAFT = {
    "market_review": "今日上证指数 3949.91 点,上涨 0.97%。",
    "key_factors": [{"title": "流动性", "detail": "央行开展逆回购操作。", "source_refs": ["来源2"]}],
    "logic_chain": [{"step": "1", "content": "指数上涨 0.97%。", "source_refs": ["来源1"]}],
    "conclusion": "上证指数报 3949.91 点,涨 0.97%,短期维持震荡上行判断。",
    "risk_tips": "注意回调风险。",
    "position_suggestion": {"action": "持有", "reason": "当前风险等级可承受一定波动"},
    "return_expectation": {"low": 5, "high": 10},
}


class FakeSource(DataSource):
    def __init__(self, name, points):
        self.name = name
        self.points = points

    async def fetch(self):
        return self.points


def install_fakes(cache) -> None:
    """覆盖 LLM 与数据源依赖:FakeLLM + 假数据源(与 conftest 共用同一 fakeredis)。"""
    app.dependency_overrides[get_llm_client] = lambda: FakeLLM([DRAFT])
    app.dependency_overrides[get_market_data_service] = lambda: MarketDataService(
        cache,
        quote_source=FakeSource("新浪财经行情", [QUOTE_POINT]),
        news_source=FakeSource("新浪财经快讯", [NEWS_POINT]),
        research_source=FakeSource("东方财富研报", []),
    )


async def _auth_headers(client, username="chat_user") -> dict:
    token = await register_and_login(client, username=username)
    return {"Authorization": f"Bearer {token}"}


async def _setup_profile(client, headers, seeded_questionnaire) -> None:
    resp = await client.post(
        "/api/v1/profile/questionnaire",
        headers=headers,
        json={"questionnaire_id": seeded_questionnaire, "answers": full_answers(QUESTIONS)},
    )
    assert resp.json()["code"] == 0


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    import json as _json

    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = _json.loads(line[len("data: ") :])
        if event and data is not None:
            events.append((event, data))
    return events


class TestChatApi:
    async def test_requires_auth(self, client):
        resp = await client.post("/api/v1/chat/sessions", json={"scenario": "market"})
        assert resp.status_code == 401
        assert resp.json()["code"] == 40101

    async def test_other_scenario_not_open(self, client):
        headers = await _auth_headers(client)
        resp = await client.post("/api/v1/chat/sessions", json={"scenario": "stock"}, headers=headers)
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001
        assert "尚未开放" in resp.json()["message"]

    async def test_full_sse_flow_advice_persisted(self, client, cache, seeded_questionnaire):
        install_fakes(cache)
        headers = await _auth_headers(client, username="chat_flow")
        await _setup_profile(client, headers, seeded_questionnaire)
        session = (await client.post("/api/v1/chat/sessions", json={"scenario": "market"}, headers=headers)).json()
        assert session["code"] == 0
        session_id = session["data"]["session_id"]

        # SSE 流式咨询:meta → delta(取数/生成/校验/合规)→ result → done
        streamed = ""
        async with client.stream(
            "POST", f"/api/v1/chat/sessions/{session_id}/messages",
            json={"content": "今天大盘怎么样?"}, headers=headers,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            async for line in response.aiter_lines():
                streamed += line + "\n"
        events = _parse_sse(streamed)
        kinds = [event for event, _ in events]
        assert kinds[0] == "meta"
        assert kinds[0] == "meta" and "delta" in kinds and kinds[-2] == "result" and kinds[-1] == "done"
        meta = events[0][1]
        assert meta["agents"] == ["宏观研究"]
        result = next(data for event, data in events if event == "result")
        advice_id = result["advice_id"]
        assert result["compliance_status"] == "passed"
        assert "3949.91" in result["conclusion"]

        # 历史消息:用户 + 助手各一条,助手携带 advice_id
        history = (await client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=headers)).json()
        items = history["data"]["items"]
        assert [item["role"] for item in items] == ["user", "assistant"]
        assert items[1]["advice_id"] == advice_id

        # 建议详情:四要素 + 逻辑链 + 溯源引用(AC-1/AC-3)
        detail = (await client.get(f"/api/v1/advice/{advice_id}", headers=headers)).json()
        assert detail["code"] == 0
        data = detail["data"]
        assert data["scenario"] == "market"
        assert data["conclusion"] == result["conclusion"]
        assert len(data["logic_chain"]) == 1
        assert "仅供参考,不构成投资建议" in data["risk_tips"]
        assert data["position_suggestion"]["risk_level"] == "C4"
        assert data["citations"][0]["verified"] is True
        assert data["citations"][-1]["source_type"] == "profile"

    async def test_send_to_missing_session_404(self, client, cache):
        install_fakes(cache)
        headers = await _auth_headers(client, username="chat_404")
        resp = await client.post(
            "/api/v1/chat/sessions/999/messages", json={"content": "大盘?"}, headers=headers
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == 40401

    async def test_without_profile_error_event_in_sse(self, client, cache):
        install_fakes(cache)
        headers = await _auth_headers(client, username="chat_no_profile")
        session = (await client.post("/api/v1/chat/sessions", json={"scenario": "market"}, headers=headers)).json()
        session_id = session["data"]["session_id"]
        streamed = ""
        async with client.stream(
            "POST", f"/api/v1/chat/sessions/{session_id}/messages",
            json={"content": "今天大盘怎么样?"}, headers=headers,
        ) as response:
            async for line in response.aiter_lines():
                streamed += line + "\n"
        events = _parse_sse(streamed)
        error = next((data for event, data in events if event == "error"), None)
        assert error is not None
        assert error["code"] == 40001
        assert "请先完成画像建立" in error["message"]

    async def test_advice_detail_not_found_or_foreign(self, client, cache):
        install_fakes(cache)
        headers = await _auth_headers(client, username="chat_foreign")
        resp = await client.get("/api/v1/advice/999", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == 40401
