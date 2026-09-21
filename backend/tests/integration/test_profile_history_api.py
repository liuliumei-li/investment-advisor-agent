"""画像更新历史接口集成测试(US-05):GET /api/v1/profile/history 分页、触发留痕与错误码。"""

from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.llm import get_llm_client
from app.main import app
from tests.helpers import FakeLLM, full_answers, register_and_login

QUESTIONS = QUESTIONNAIRE_V1["questions"]


async def _auth_headers(client, username="history_user") -> dict:
    token = await register_and_login(client, username=username)
    return {"Authorization": f"Bearer {token}"}


async def _submit_questionnaire(client, headers, seeded_questionnaire) -> None:
    resp = await client.post(
        "/api/v1/profile/questionnaire",
        headers=headers,
        json={"questionnaire_id": seeded_questionnaire, "answers": full_answers(QUESTIONS)},
    )
    assert resp.json()["code"] == 0


class TestHistoryApi:
    async def test_requires_auth(self, client):
        resp = await client.get("/api/v1/profile/history")
        assert resp.status_code == 401
        assert resp.json()["code"] == 40101

    async def test_empty_history_for_new_user(self, client):
        headers = await _auth_headers(client, username="no_events_user")
        resp = await client.get("/api/v1/profile/history", headers=headers)
        body = resp.json()
        assert body["code"] == 0
        assert body["data"] == {"items": [], "total": 0, "page": 1, "page_size": 20}

    async def test_invalid_page_rejected(self, client):
        headers = await _auth_headers(client, username="bad_page_user")
        resp = await client.get("/api/v1/profile/history", headers=headers, params={"page": 0})
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_invalid_page_size_rejected(self, client):
        headers = await _auth_headers(client, username="bad_size_user")
        resp = await client.get("/api/v1/profile/history", headers=headers, params={"page_size": 101})
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_full_flow_history_latest_first_with_pagination(
        self, client, seeded_questionnaire
    ):
        headers = await _auth_headers(client, username="flow_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)  # v1 问卷测评

        # v2 对话更新(风险等级冲突 + 持仓习惯采纳)
        app.dependency_overrides[get_llm_client] = lambda: FakeLLM(
            [
                {
                    "risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"},
                    "holding_habit": {"summary": "重仓白酒", "evidence": "我主要拿的是白酒股"},
                    "return_expectation": {"low": 6, "high": 10, "evidence": "希望年化 6% 到 10%"},
                    "investment_horizon": {"value": "中期", "evidence": "大概两年"},
                }
            ]
        )
        resp = await client.post(
            "/api/v1/profile/dialog",
            headers=headers,
            json={"message": "我完全不想亏钱,希望年化 6% 到 10%,大概两年,我主要拿的是白酒股"},
        )
        assert resp.json()["code"] == 0

        # v3 用户修正(解决冲突)
        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={"amendments": [{"field": "risk_level", "value": "C1"}]},
        )
        assert resp.json()["code"] == 0

        # v4 用户确认
        resp = await client.put("/api/v1/profile", headers=headers, json={"confirm": True})
        assert resp.json()["code"] == 0

        # AC-3:历史可追溯,最新在前
        page1 = (await client.get("/api/v1/profile/history", headers=headers, params={"page_size": 3})).json()
        assert page1["code"] == 0
        data = page1["data"]
        assert data["total"] == 4
        assert data["page_size"] == 3
        items = data["items"]
        assert [item["trigger"] for item in items] == ["用户确认", "用户修正", "对话更新"]
        assert [item["version"] for item in items] == [4, 3, 2]
        assert all(item["created_at"] for item in items)

        amend_event = items[1]
        assert amend_event["changes"] == [
            {"field": "risk_level", "before": "C4", "after": "C1", "source": "用户修正", "quote": None}
        ]

        dialog_event = items[2]
        assert dialog_event["conflicts"] == [
            {
                "field": "risk_level",
                "current": "C4",
                "proposed": "C1",
                "source": "对话",
                "quote": "我完全不想亏钱",
            }
        ]
        habit_change = next(c for c in dialog_event["changes"] if c["field"] == "holding_habit_summary")
        assert habit_change["after"] == "重仓白酒"

        # 第二页:问卷测评事件
        page2 = (
            await client.get("/api/v1/profile/history", headers=headers, params={"page": 2, "page_size": 3})
        ).json()
        items2 = page2["data"]["items"]
        assert [item["trigger"] for item in items2] == ["问卷测评"]
        assert items2[0]["changes"][0]["field"] == "risk_level"
        assert items2[0]["changes"][0]["before"] is None
