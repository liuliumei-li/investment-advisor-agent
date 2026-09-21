"""画像报告接口集成测试(US-04):GET /report、GET /profile、PUT /profile 全链路与错误码。"""

from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from app.llm import get_llm_client
from app.main import app
from tests.helpers import FakeLLM, full_answers, register_and_login

QUESTIONS = QUESTIONNAIRE_V1["questions"]

FOUR_HOLDINGS = [
    {"asset_type": "stock", "code": "600519", "name": "贵州茅台", "quantity": "4", "cost_price": "10"},
    {"asset_type": "stock", "code": "000858", "name": "五粮液", "quantity": "3", "cost_price": "10"},
    {"asset_type": "etf", "code": "510300", "name": "沪深300ETF", "quantity": "2", "cost_price": "10"},
    {"asset_type": "fund", "code": "000001", "name": "某基金", "quantity": "1", "cost_price": "10"},
]  # 市值 40/30/20/10 → 前三集中度 0.90 → 持仓习惯轴分数 10


async def _auth_headers(client, username="report_user") -> dict:
    token = await register_and_login(client, username=username)
    return {"Authorization": f"Bearer {token}"}


async def _submit_questionnaire(client, headers, seeded_questionnaire) -> None:
    resp = await client.post(
        "/api/v1/profile/questionnaire",
        headers=headers,
        json={"questionnaire_id": seeded_questionnaire, "answers": full_answers(QUESTIONS)},
    )
    assert resp.json()["code"] == 0


class TestAuthRequired:
    async def test_report_requires_auth(self, client):
        resp = await client.get("/api/v1/profile/report")
        assert resp.status_code == 401
        assert resp.json()["code"] == 40101

    async def test_get_profile_requires_auth(self, client):
        resp = await client.get("/api/v1/profile")
        assert resp.status_code == 401
        assert resp.json()["code"] == 40101

    async def test_put_profile_requires_auth(self, client):
        resp = await client.put("/api/v1/profile", json={"confirm": True})
        assert resp.status_code == 401
        assert resp.json()["code"] == 40101


class TestReportFlow:
    async def test_questionnaire_report_confirm_flow(self, client, seeded_questionnaire):
        headers = await _auth_headers(client)
        await _submit_questionnaire(client, headers, seeded_questionnaire)

        # AC-1/AC-2:报告含四要素维度、雷达分数与问卷来源溯源
        resp = await client.get("/api/v1/profile/report", headers=headers)
        body = resp.json()
        assert body["code"] == 0
        report = body["data"]
        assert report["confirmed"] is False
        assert report["incomplete_sources"] == ["对话", "持仓"]
        assert report["conflicts"] == []
        risk, expectation, horizon, habit = report["dimensions"]
        assert risk["display"] == "C4" and risk["display_label"] == "进取型"
        assert risk["score"] == 80
        assert risk["source"] == "问卷" and risk["source_version"] == 1
        assert risk["updated_at"]  # BR-DAT-04 溯源时间戳
        assert expectation["display"] == "6%~10%" and expectation["score"] == 32
        assert horizon["display"] == "中期" and horizon["score"] == 67
        assert habit["display"] is None and habit["score"] is None

        # AC-3:确认画像 → 报告与当前画像均反映 confirmed
        resp = await client.put("/api/v1/profile", headers=headers, json={"confirm": True})
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["confirmed"] is True
        assert body["data"]["applied_amendments"] == []

        resp = await client.get("/api/v1/profile/report", headers=headers)
        assert resp.json()["data"]["confirmed"] is True

        # AC-4:当前画像随时可查,与报告一致
        resp = await client.get("/api/v1/profile", headers=headers)
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["risk_level"] == "C4"
        assert body["data"]["version"] == 2
        assert body["data"]["confirmed"] is True

    async def test_dialog_conflict_then_amend(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="conflict_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        # 三要素齐备一次收口:对话主张 C1 与问卷 C4 冲突;其余要素与问卷一致不冲突
        app.dependency_overrides[get_llm_client] = lambda: FakeLLM(
            [
                {
                    "risk_tolerance": {"level": "C1", "evidence": "我完全不想亏钱"},
                    "return_expectation": {"low": 6, "high": 10, "evidence": "希望年化 6% 到 10%"},
                    "investment_horizon": {"value": "中期", "evidence": "大概两年"},
                }
            ]
        )
        resp = await client.post(
            "/api/v1/profile/dialog",
            headers=headers,
            json={"message": "我完全不想亏钱,希望年化 6% 到 10%,大概两年", "finish": False},
        )
        assert resp.json()["code"] == 0

        resp = await client.get("/api/v1/profile/report", headers=headers)
        conflicts = resp.json()["data"]["conflicts"]
        assert len(conflicts) == 1
        assert conflicts[0]["field"] == "risk_level"
        assert conflicts[0]["proposed"] == "C1"
        assert conflicts[0]["quote"] == "我完全不想亏钱"

        # AC-3:修正风险等级 → 立即生效,冲突解决
        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={"amendments": [{"field": "risk_level", "value": "C1"}]},
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["risk_level"] == "C1"
        assert body["data"]["conflicts_remaining"] == []

        resp = await client.get("/api/v1/profile/report", headers=headers)
        report = resp.json()["data"]
        assert report["conflicts"] == []
        assert report["dimensions"][0]["display"] == "C1"
        assert report["dimensions"][0]["source"] == "用户修正"

    async def test_holdings_axis_score_from_import(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="holdings_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        resp = await client.post(
            "/api/v1/profile/import",
            headers=headers,
            json={"mode": "list", "holdings": FOUR_HOLDINGS},
        )
        assert resp.json()["code"] == 0

        resp = await client.get("/api/v1/profile/report", headers=headers)
        report = resp.json()["data"]
        habit = report["dimensions"][3]
        assert habit["display"]  # 持仓习惯摘要
        assert habit["score"] == 10  # (1 - 0.90) * 100
        assert habit["score_updated_at"]
        assert habit["source"] == "持仓"
        assert report["source_mix"] == {"questionnaire": 0.5, "holdings": 0.5}
        assert report["incomplete_sources"] == ["对话"]

    async def test_report_404_without_profile(self, client):
        headers = await _auth_headers(client, username="empty_user")
        resp = await client.get("/api/v1/profile/report", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == 40401

    async def test_get_profile_404_without_profile(self, client):
        headers = await _auth_headers(client, username="empty_user2")
        resp = await client.get("/api/v1/profile", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == 40401

    async def test_put_404_without_profile(self, client):
        headers = await _auth_headers(client, username="empty_user3")
        resp = await client.put("/api/v1/profile", headers=headers, json={"confirm": True})
        assert resp.status_code == 404
        assert resp.json()["code"] == 40401

    async def test_put_empty_payload_rejected(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="empty_payload_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        resp = await client.put("/api/v1/profile", headers=headers, json={})
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_put_invalid_field_rejected(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="bad_field_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={"amendments": [{"field": "username", "value": "x"}]},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_put_invalid_risk_level_rejected(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="bad_level_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={"amendments": [{"field": "risk_level", "value": "C9"}]},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_put_inverted_return_range_rejected(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="bad_range_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={"amendments": [{"field": "return_expectation", "value": {"low": 10, "high": 5}}]},
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001

    async def test_put_duplicate_field_rejected(self, client, seeded_questionnaire):
        headers = await _auth_headers(client, username="dup_user")
        await _submit_questionnaire(client, headers, seeded_questionnaire)
        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={
                "amendments": [
                    {"field": "investment_horizon", "value": "长期"},
                    {"field": "investment_horizon", "value": "短期"},
                ]
            },
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == 40001
