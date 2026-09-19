"""profile 接口集成测试:US-01 全链路(取问卷 → 提交 → 结果再查)与鉴权(AC-2/AC-3、UC-01)。"""

from app.data.questionnaire_v1 import QUESTIONNAIRE_V1
from tests.helpers import full_answers, register_and_login

QUESTIONS = QUESTIONNAIRE_V1["questions"]


class TestAuthRequired:
    async def test_latest_questionnaire_requires_auth(self, client):
        resp = await client.get("/api/v1/profile/questionnaires/latest")
        body = resp.json()
        assert resp.status_code == 401
        assert body["code"] == 40101
        assert body["message"] == "未认证"

    async def test_submit_requires_auth(self, client):
        resp = await client.post(
            "/api/v1/profile/questionnaire",
            json={"questionnaire_id": 1, "answers": [{"question_id": "rt1", "option_id": "a"}]},
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == 40101


class TestQuestionnaireFlow:
    async def test_full_flow_get_submit_review(self, client, seeded_questionnaire):
        token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # AC-1:获取最新问卷,14 题覆盖四个维度
        resp = await client.get("/api/v1/profile/questionnaires/latest", headers=headers)
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["id"] == seeded_questionnaire
        assert body["data"]["version"] == 1
        assert len(body["data"]["questions"]) == 14
        assert {q["dimension"] for q in body["data"]["questions"]} == {
            "risk_tolerance",
            "return_expectation",
            "investment_horizon",
            "investment_experience",
        }

        # AC-2:提交答卷 → 自动生成风险等级与画像要素(全 c:总分 60 → C4)
        resp = await client.post(
            "/api/v1/profile/questionnaire",
            headers=headers,
            json={"questionnaire_id": seeded_questionnaire, "answers": full_answers(QUESTIONS)},
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["score"] == 60
        assert body["data"]["risk_level"] == "C4"
        assert body["data"]["risk_level_name"] == "进取型"
        assert body["data"]["profile"]["investment_horizon"] == "中期"
        assert body["data"]["profile"]["confirmed"] is False

        # AC-3:测评结果与账户关联保存,可再次查看
        resp = await client.get("/api/v1/profile/questionnaire/latest-response", headers=headers)
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["score"] == 60
        assert body["data"]["risk_level"] == "C4"
        assert body["data"]["risk_level_name"] == "进取型"
        assert len(body["data"]["answers"]) == 14

    async def test_submit_incomplete_answers_rejected(self, client, seeded_questionnaire):
        token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/profile/questionnaire",
            headers=headers,
            json={
                "questionnaire_id": seeded_questionnaire,
                "answers": full_answers(QUESTIONS)[:-1],
            },
        )
        body = resp.json()
        assert resp.status_code == 400
        assert body["code"] == 40001
        assert "作答不完整" in body["message"]

    async def test_submit_stale_questionnaire_rejected(self, client):
        # 库中无问卷时提交任意 id:视为问卷不存在/已更新
        token = await register_and_login(client)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            "/api/v1/profile/questionnaire",
            headers=headers,
            json={"questionnaire_id": 999, "answers": full_answers(QUESTIONS)},
        )
        body = resp.json()
        assert resp.status_code == 404
        assert body["code"] == 40401
