"""POST /api/v1/profile/dialog 集成测试:多轮追问 → 收口合并,统一信封与错误码(US-02)。"""

from sqlalchemy import select

from app.core.exceptions import LLMServiceError
from app.llm import get_llm_client
from app.main import app
from app.models.user_profile import UserProfile
from tests.helpers import FakeLLM, register_and_login


class _BrokenLLM:
    async def chat_json(self, messages, *, temperature=0.2, purpose="general"):
        raise LLMServiceError("LLM 调用失败(重试耗尽)")


async def _auth_headers(client, username="dialog_user") -> dict:
    token = await register_and_login(client, username=username)
    return {"Authorization": f"Bearer {token}"}


class TestDialogApi:
    async def test_unauthorized_rejected(self, client):
        resp = await client.post("/api/v1/profile/dialog", json={"message": "你好"})
        assert resp.json()["code"] == 40101

    async def test_empty_message_rejected(self, client):
        headers = await _auth_headers(client)
        resp = await client.post("/api/v1/profile/dialog", json={"message": ""}, headers=headers)
        assert resp.json()["code"] == 40001

    async def test_llm_failure_returns_50004(self, client):
        app.dependency_overrides[get_llm_client] = lambda: _BrokenLLM()
        headers = await _auth_headers(client)
        resp = await client.post("/api/v1/profile/dialog", json={"message": "我想稳一点"}, headers=headers)
        assert resp.json()["code"] == 50004

    async def test_clarification_then_completion(self, client, session_factory):
        fake = FakeLLM(
            [
                {"risk_tolerance": {"level": "C3", "evidence": "我能承受 20% 回撤"}},
                {
                    "return_expectation": {"low": 10, "high": 15, "evidence": "希望年化 10% 到 15%"},
                    "investment_horizon": {"value": "长期", "evidence": "打算放三年"},
                },
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: fake
        headers = await _auth_headers(client)

        first = await client.post("/api/v1/profile/dialog", json={"message": "我能承受 20% 回撤"}, headers=headers)
        body = first.json()
        assert body["code"] == 0 and body["trace_id"]
        data = body["data"]
        assert data["needs_clarification"] is True and data["completed"] is False
        assert data["session_id"]
        assert data["slots"]["risk_tolerance"]["level"] == "C3"
        # 风险承受已抽取,追问剩余两要素
        assert "回撤" not in data["reply"] and "年化收益率" in data["reply"]

        second = await client.post(
            "/api/v1/profile/dialog",
            json={"session_id": data["session_id"], "message": "希望年化 10% 到 15%,打算放三年"},
            headers=headers,
        )
        body = second.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["completed"] is True and data["needs_clarification"] is False
        updates = data["profile_updates"]
        assert len(updates) == 3
        assert all(u["applied"] and u["quote"] for u in updates)  # AC-4:每项带原文引用
        assert len(fake.calls) == 2

        # 画像已落库(BR-IMG-02 要素:风险等级/收益预期/投资期限)
        async with session_factory() as session:
            profile = (await session.execute(select(UserProfile))).scalars().first()
        assert profile.risk_level.value == "C3"
        assert float(profile.return_expectation_low) == 10
        assert float(profile.return_expectation_high) == 15
        assert profile.investment_horizon == "长期"
        assert profile.source_mix == {"dialog": 1.0}

    async def test_fabricated_evidence_dropped_and_asked_again(self, client):
        fake = FakeLLM(
            [
                {"risk_tolerance": {"level": "C4", "evidence": "我完全没说过的话"}},
                {"risk_tolerance": {"level": "C4", "evidence": "回撤 30% 也能接受"}},
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: fake
        headers = await _auth_headers(client)

        first = await client.post("/api/v1/profile/dialog", json={"message": "回撤 30% 也能接受"}, headers=headers)
        data = first.json()["data"]
        assert data["needs_clarification"] is True
        assert data["slots"] == {}  # 编造引用被丢弃
        assert "回撤" in data["reply"]  # 风险承受重新追问

        second = await client.post(
            "/api/v1/profile/dialog",
            json={"session_id": data["session_id"], "message": "嗯,就是回撤 30% 也能接受"},
            headers=headers,
        )
        assert second.json()["data"]["slots"]["risk_tolerance"]["level"] == "C4"
