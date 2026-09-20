"""持仓导入与分析 API 集成测试(US-03 Task 4):清单/CSV/文本三种方式 + 画像合并 + 错误提示。"""

from sqlalchemy import select

from app.llm import get_llm_client
from app.main import app
from app.models.user_profile import UserProfile
from tests.helpers import FakeLLM, full_answers, register_and_login


async def _auth_headers(client, username="holder") -> dict:
    token = await register_and_login(client, username=username)
    return {"Authorization": f"Bearer {token}"}


def _list_payload():
    return {
        "mode": "list",
        "holdings": [
            {"asset_type": "stock", "code": "600519", "name": "贵州茅台", "quantity": "100", "cost_price": "1500"},
            {"asset_type": "etf", "code": "510300", "name": "沪深300ETF", "quantity": "1000", "cost_price": "4"},
        ],
    }


class TestHoldingsImportApi:
    async def test_unauthorized_rejected(self, client):
        resp = await client.post("/api/v1/profile/import", json=_list_payload())
        assert resp.json()["code"] == 40101

    async def test_list_import_creates_profile_from_holdings(self, client, session_factory):
        headers = await _auth_headers(client)
        resp = await client.post("/api/v1/profile/import", json=_list_payload(), headers=headers)
        body = resp.json()
        assert body["code"] == 0 and body["trace_id"]
        data = body["data"]
        assert data["holding_count"] == 2
        assert data["analysis"]["holding_count"] == 2
        assert data["analysis"]["inferred_risk_level"] == "C5"  # 股票占比 97.4%
        assert data["turnover"]["has_history"] is False
        assert data["risk_deviation"] is None  # 新画像,无自评可比
        assert set(data["incomplete_sources"]) == {"问卷", "对话"}
        fields = {u["field"]: u for u in data["profile_updates"]}
        assert fields["risk_level"]["after"] == "C5"

        async with session_factory() as session:
            profile = (await session.execute(select(UserProfile))).scalars().first()
        assert profile.risk_level.value == "C5"
        assert profile.source_mix == {"holdings": 1.0}
        assert profile.holding_habit_summary  # 持仓习惯摘要已生成(BR-IMG-02 第四要素)

    async def test_deviation_prompt_when_self_assessed_differs(self, client, seeded_questionnaire, session_factory):
        headers = await _auth_headers(client)
        resp = await client.get("/api/v1/profile/questionnaires/latest", headers=headers)
        questions = resp.json()["data"]["questions"]
        submit = await client.post(
            "/api/v1/profile/questionnaire",
            json={
                "questionnaire_id": seeded_questionnaire,
                "answers": full_answers(questions, option_id="a"),  # 总分 20 → C1
            },
            headers=headers,
        )
        assert submit.json()["code"] == 0

        resp = await client.post("/api/v1/profile/import", json=_list_payload(), headers=headers)
        data = resp.json()["data"]
        deviation = data["risk_deviation"]
        assert deviation is not None
        assert deviation["assessed_risk_level"] == "C1"
        assert deviation["portfolio_risk_level"] == "C5"
        assert "偏差明显" in deviation["message"]

        async with session_factory() as session:
            profile = (await session.execute(select(UserProfile))).scalars().first()
        assert profile.risk_level.value == "C1"  # 自评不被持仓覆盖
        assert profile.version == 2
        assert profile.source_mix == {"questionnaire": 0.5, "holdings": 0.5}

    async def test_csv_upload_import(self, client):
        headers = await _auth_headers(client, username="csv_user")
        csv_data = "asset_type,code,name,quantity,cost_price\nstock,600519,贵州茅台,100,1500\n".encode()
        resp = await client.post(
            "/api/v1/profile/import/csv",
            files={"file": ("holdings.csv", csv_data, "text/csv")},
            headers=headers,
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["source"] == "csv"
        assert body["data"]["holding_count"] == 1

    async def test_text_import_via_llm(self, client):
        fake = FakeLLM(
            [
                {
                    "holdings": [
                        {
                            "asset_type": "stock",
                            "code": "300750",
                            "name": "宁德时代",
                            "quantity": 100,
                            "cost_price": 200,
                        }
                    ]
                }
            ]
        )
        app.dependency_overrides[get_llm_client] = lambda: fake
        headers = await _auth_headers(client, username="text_user")
        resp = await client.post(
            "/api/v1/profile/import",
            json={"mode": "text", "text": "我买了 100 股宁德时代,成本 200"},
            headers=headers,
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["source"] == "text"
        assert body["data"]["holding_count"] == 1

    async def test_csv_missing_column_returns_40001(self, client):
        headers = await _auth_headers(client, username="bad_csv")
        csv_data = "code,name\n600519,贵州茅台\n".encode()
        resp = await client.post(
            "/api/v1/profile/import/csv",
            files={"file": ("holdings.csv", csv_data, "text/csv")},
            headers=headers,
        )
        body = resp.json()
        assert body["code"] == 40001
        assert "资产类别" in body["message"]

    async def test_invalid_row_reports_position(self, client):
        headers = await _auth_headers(client, username="bad_row")
        payload = _list_payload()
        payload["holdings"][1]["quantity"] = "0"
        resp = await client.post("/api/v1/profile/import", json=payload, headers=headers)
        body = resp.json()
        assert body["code"] == 40001
        assert "清单第 2 条" in body["message"]

    async def test_empty_list_rejected(self, client):
        headers = await _auth_headers(client, username="empty_list")
        resp = await client.post("/api/v1/profile/import", json={"mode": "list", "holdings": []}, headers=headers)
        assert resp.json()["code"] == 40001
